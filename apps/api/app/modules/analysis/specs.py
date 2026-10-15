import json
import math
import re
from collections.abc import Hashable
from typing import Any, Literal
from urllib.parse import unquote
from uuid import UUID

import yaml
from openapi_spec_validator import OpenAPIV30SpecValidator
from yaml.events import AliasEvent

from app.core.errors import DomainError
from app.modules.analysis.schemas import Operation, ParsedSpec, ParserLimits
from app.modules.projects.schemas import literal_base_url

METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})


def invalid(code: str, message: str) -> DomainError:
    return DomainError(422, code, message)


def pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def resolve_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("Invalid internal pointer")
    result = document
    for part in pointer[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise ValueError("Invalid pointer escape")
        token = part.replace("~1", "/").replace("~0", "~")
        if isinstance(result, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                raise ValueError("Invalid array pointer")
            result = result[int(token)]
        elif isinstance(result, dict):
            result = result[token]
        else:
            raise ValueError("Pointer does not resolve")
    return result


def duplicate_checked(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise invalid("duplicate_key", "Duplicate document keys are not allowed")
        result[key] = value
    return result


class BoundedLoader(yaml.SafeLoader):
    def __init__(self, source: str, limits: ParserLimits) -> None:
        super().__init__(source)
        self.limits = limits
        self.node_count = 0
        self.depth = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            raise invalid("yaml_alias", "YAML aliases are not supported")
        self.node_count += 1
        self.depth += 1
        try:
            if (
                self.node_count > self.limits.yaml_max_nodes
                or self.depth > self.limits.yaml_max_depth
            ):
                raise invalid(
                    "document_complexity", "Document nesting or node count exceeds the limit"
                )
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Hashable, Any]:
        pairs = []
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise invalid("invalid_key", "Document keys must be strings")
            pairs.append((key, self.construct_object(value_node, deep=deep)))
        result: dict[Hashable, Any] = {}
        result.update(duplicate_checked(pairs))
        return result


def inspect_document(document: Any, limits: ParserLimits) -> None:
    stack = [(document, 1)]
    count = 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if depth > limits.yaml_max_depth or count > limits.yaml_max_nodes:
            raise invalid("document_complexity", "Document nesting or node count exceeds the limit")
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise invalid("invalid_key", "Document keys must be strings")
                if key == "$ref":
                    if not isinstance(item, str) or not item.startswith("#/"):
                        raise invalid(
                            "external_reference", "Only internal #/ references are supported"
                        )
                    try:
                        resolve_pointer(document, unquote(item[1:]))
                    except (ValueError, KeyError, IndexError):
                        raise invalid(
                            "invalid_reference", "An internal reference does not resolve"
                        ) from None
                stack.append((item, depth + 1))
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
        elif value is not None and type(value) not in {str, int, float, bool}:
            raise invalid("invalid_value", "Only JSON-compatible document values are supported")
        elif isinstance(value, float) and not math.isfinite(value):
            raise invalid("invalid_value", "Non-finite numbers are not supported")


def parse_spec(
    data: bytes,
    file_id: UUID,
    limits: ParserLimits,
    *,
    role: Literal["old_spec", "new_spec"] = "old_spec",
    api_base_url_override: str | None = None,
) -> ParsedSpec:
    if len(data) > limits.max_spec_bytes:
        raise DomainError(413, "input_too_large", "Specification exceeds the byte limit")
    try:
        source = data.decode("utf-8")
        if source.lstrip().startswith(("{", "[")):
            document = json.loads(
                source,
                object_pairs_hook=duplicate_checked,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        else:
            loader = BoundedLoader(source, limits)
            try:
                document = loader.get_single_data()
            finally:
                loader.dispose()
    except (UnicodeError, ValueError, yaml.YAMLError, RecursionError):
        raise invalid(
            "invalid_spec", "Specification must be valid single-document UTF-8 JSON or YAML"
        ) from None
    inspect_document(document, limits)
    if not isinstance(document, dict) or not re.fullmatch(
        r"3\.0\.[0-9]+", str(document.get("openapi", ""))
    ):
        raise invalid("unsupported_openapi", "Only OpenAPI 3.0.x specifications are supported")
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise invalid("invalid_openapi", "Specification paths must be an object")
    indexed_items = []
    count = 0
    for path, item in paths.items():
        pointer = f"/paths/{pointer_token(path)}"
        seen = set()
        while isinstance(item, dict) and "$ref" in item:
            reference = item["$ref"]
            if reference in seen or any(method in item for method in METHODS):
                raise invalid("invalid_reference", "Path-item references must be unambiguous")
            seen.add(reference)
            pointer = unquote(reference[1:])
            item = resolve_pointer(document, pointer)
        if not isinstance(item, dict):
            raise invalid("invalid_openapi", "Path items must be objects")
        count += sum(method in item for method in METHODS)
        if count > limits.max_spec_operations:
            raise invalid("operation_limit", "Specification exceeds the operation limit")
        indexed_items.append((path, item, pointer))
    try:
        OpenAPIV30SpecValidator(document).validate()
    except Exception:
        raise invalid(
            "invalid_openapi", "Specification does not satisfy OpenAPI 3.0 validation"
        ) from None
    operations = {}
    for path, item, pointer in indexed_items:
        for method in METHODS:
            if method in item:
                key = f"{method.upper()} {path}"
                try:
                    operations[key] = Operation(
                        key=key,
                        path=path,
                        method=method.upper(),
                        json_pointer=f"{pointer}/{method}",
                        operation_id=item[method].get("operationId"),
                    )
                except ValueError:
                    raise invalid(
                        "unsupported_operation", "Operation exceeds the supported contract"
                    ) from None
    servers = document.get("servers", [])
    base_url = api_base_url_override or (servers[0].get("url") if servers else None)
    warnings = []
    try:
        if base_url and len(base_url) > 2048:
            raise ValueError("Base URL exceeds the limit")
        base_url = literal_base_url(base_url) if base_url else None
    except ValueError:
        if api_base_url_override is not None:
            raise invalid("invalid_base_url", "API base URL override must be literal") from None
        base_url = None
    if role == "old_spec" and base_url is None:
        raise invalid(
            "api_base_url_required", "A confirmed literal API base URL override is required"
        )
    if len(servers) > 1 and not api_base_url_override:
        warnings.append("The first declared server is used; confirm or override the API base URL")
    return ParsedSpec(
        file_id=file_id,
        document=document,
        operations=operations,
        api_base_url=base_url,
        warnings=warnings,
    )
