import re
from collections.abc import Iterator
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from app.modules.analysis.schemas import CallSite, ExtractionResult, SourceSpan

VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}
FUNCTIONS = {
    "function_declaration",
    "function_expression",
    "arrow_function",
    "method_definition",
    "generator_function_declaration",
    "generator_function",
}


def walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        item = stack.pop()
        yield item
        stack.extend(reversed(item.named_children))


def text(node: Node, data: bytes) -> str:
    return data[node.start_byte : node.end_byte].decode("utf-8")


def literal(value: str) -> str:
    if len(value) < 2 or value[0] not in "\"'`" or value[-1] != value[0]:
        raise ValueError("Unsupported string")
    body = value[1:-1]
    result = []
    index = 0
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
    while index < len(body):
        char = body[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue
        index += 1
        if index >= len(body):
            raise ValueError("Incomplete escape")
        char = body[index]
        if char in "\\\"'`/$":
            result.append(char)
        elif char in escapes:
            result.append(escapes[char])
        elif char in {"u", "x"}:
            length = 4 if char == "u" else 2
            digits = body[index + 1 : index + 1 + length]
            if len(digits) != length or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                raise ValueError("Unsupported escape")
            result.append(chr(int(digits, 16)))
            index += length
        else:
            raise ValueError("Unsupported escape")
        index += 1
    return "".join(result).encode("utf-16", "surrogatepass").decode("utf-16")


def url_pattern(node: Node, data: bytes) -> str:
    if node.type == "string":
        return literal(text(node, data))
    if node.type != "template_string":
        raise ValueError("dynamic_url")
    raw = text(node, data)
    substitutions = [
        child for child in node.named_children if child.type == "template_substitution"
    ]
    for index, child in reversed(list(enumerate(substitutions))):
        start, end = child.start_byte - node.start_byte, child.end_byte - node.start_byte
        encoded = raw.encode("utf-8")
        encoded = encoded[:start] + f"{{argument_{index}}}".encode() + encoded[end:]
        raw = encoded.decode("utf-8")
    pattern = literal(raw)
    if "{" in urlsplit(pattern).netloc:
        raise ValueError("invalid_template")
    path = urlsplit(pattern).path
    for part in path.split("/"):
        if ("{" in part or "}" in part) and not re.fullmatch(r"\{argument_[0-9]+\}", part):
            raise ValueError("invalid_template")
    if re.search(r"\{argument_[0-9]+\}", urlsplit(pattern).query + urlsplit(pattern).fragment):
        raise ValueError("invalid_template")
    return pattern


def enclosing_scope(node: Node, function_only: bool = False) -> Node:
    current = node.parent
    while current is not None:
        if (
            current.type == "program"
            or current.type in FUNCTIONS
            or (not function_only and current.type == "statement_block")
        ):
            return current
        current = current.parent
    return node


def extract(
    data: bytes, file_id: UUID, language: Literal["javascript", "typescript"]
) -> ExtractionResult:
    grammar = (
        tree_sitter_javascript.language()
        if language == "javascript"
        else tree_sitter_typescript.language_typescript()
    )
    tree = Parser(Language(grammar)).parse(data)
    nodes = list(walk(tree.root_node))
    has_jsx = any(node.type.startswith("jsx_") for node in nodes)
    shadow_scopes = []
    for node in nodes:
        binding = None
        scope = None
        if node.type == "variable_declarator":
            binding = node.child_by_field_name("name")
            scope = enclosing_scope(
                node, node.parent is not None and node.parent.type == "variable_declaration"
            )
        elif node.type in FUNCTIONS:
            params = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
            if params and any(
                text(item, data) == "fetch"
                for item in walk(params)
                if item.type in {"identifier", "shorthand_property_identifier_pattern"}
            ):
                shadow_scopes.append(node)
            name = node.child_by_field_name("name")
            if name and text(name, data) == "fetch":
                shadow_scopes.append(
                    enclosing_scope(node) if node.type.endswith("declaration") else node
                )
        elif node.type in {"class_declaration", "catch_clause"}:
            binding = node.child_by_field_name("name") or node.child_by_field_name("parameter")
            scope = node if node.type == "catch_clause" else enclosing_scope(node)
        elif node.type == "import_clause":
            binding = next(
                (child for child in node.named_children if child.type == "identifier"), None
            )
            scope = tree.root_node
        elif node.type in {"import_specifier", "namespace_import"}:
            binding = node.child_by_field_name("alias") or node.child_by_field_name("name")
            if binding is None and node.type == "namespace_import":
                binding = next(
                    (child for child in node.named_children if child.type == "identifier"), None
                )
            scope = tree.root_node
        elif node.type == "assignment_expression":
            binding = node.child_by_field_name("left")
            scope = enclosing_scope(node, True)
        if (
            binding
            and scope
            and any(
                text(item, data) == "fetch"
                for item in walk(binding)
                if item.type in {"identifier", "shorthand_property_identifier_pattern"}
            )
        ):
            shadow_scopes.append(scope)
    calls = []
    for node in nodes:
        if node.type != "call_expression":
            continue
        function = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if function is None or args is None:
            continue
        direct = function.type == "identifier" and text(function, data) == "fetch"
        wrapper = False
        if function.type == "member_expression":
            prop = function.child_by_field_name("property")
            obj = function.child_by_field_name("object")
            wrapper = bool(
                prop
                and obj
                and (
                    text(prop, data) == "fetch"
                    or (
                        text(obj, data) in {"axios", "http", "httpClient", "client"}
                        and text(prop, data).upper() in VERBS
                    )
                )
            )
        if not direct and not wrapper:
            continue
        method: str | None = "GET"
        pattern = None
        reason = "unmapped"
        try:
            if not args.named_children:
                raise ValueError("missing_url")
            pattern = url_pattern(args.named_children[0], data)
            if len(pattern) > 2048:
                pattern = None
                raise ValueError("url_pattern_limit")
            if len(args.named_children) > 1:
                options = args.named_children[1]
                if options.type != "object" or any(
                    child.type == "spread_element" for child in options.named_children
                ):
                    raise ValueError("dynamic_options")
                method_nodes = []
                for pair in options.named_children:
                    if (
                        pair.type == "shorthand_property_identifier"
                        and text(pair, data) == "method"
                    ):
                        raise ValueError("dynamic_method")
                    if pair.type == "method_definition":
                        name = pair.child_by_field_name("name")
                        if name is not None and text(name, data) == "method":
                            raise ValueError("dynamic_method")
                    key = pair.child_by_field_name("key")
                    if key is not None:
                        if key.type == "computed_property_name":
                            raise ValueError("dynamic_options")
                        key_text = (
                            literal(text(key, data)) if key.type == "string" else text(key, data)
                        )
                        if key_text == "method":
                            method_nodes.append(pair.child_by_field_name("value"))
                if len(method_nodes) > 1 or (
                    method_nodes and (method_nodes[0] is None or method_nodes[0].type != "string")
                ):
                    raise ValueError("dynamic_method")
                if method_nodes:
                    method_node = method_nodes[0]
                    assert method_node is not None
                    method = literal(text(method_node, data)).upper()
                    if method not in VERBS:
                        raise ValueError("unsupported_method")
        except (ValueError, UnicodeError) as error:
            reason = (
                str(error)
                if str(error)
                in {
                    "missing_url",
                    "dynamic_url",
                    "invalid_template",
                    "dynamic_options",
                    "dynamic_method",
                    "unsupported_method",
                    "url_pattern_limit",
                }
                else "unsupported_string"
            )
            method = None
        if wrapper:
            reason = "unsupported_wrapper"
            method = None
        elif any(
            scope.start_byte <= node.start_byte and node.end_byte <= scope.end_byte
            for scope in shadow_scopes
        ):
            reason = "shadowed_fetch"
        if tree.root_node.has_error:
            reason = "syntax_error"
        if has_jsx:
            reason = "unsupported_jsx"
        calls.append(
            CallSite(
                id="call_" + uuid5(file_id, f"{node.start_byte}:{node.end_byte}").hex,
                file_id=file_id,
                language=language,
                span=SourceSpan(
                    byte_start=node.start_byte,
                    byte_end=node.end_byte,
                    line_start=node.start_point.row + 1,
                    line_end=node.end_point.row + 1,
                ),
                method=method,
                url_pattern=pattern,
                unresolved_reason=reason,
            )
        )
    return ExtractionResult(
        file_id=file_id,
        language=language,
        callsites=calls,
        errors=(
            ["JSX/TSX content is not supported"]
            if has_jsx
            else ["Source contains syntax errors"]
            if tree.root_node.has_error
            else []
        ),
        limitations=["Direct fetch only; no wrappers, import resolution or value propagation"],
    )
