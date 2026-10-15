import ast
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from app.modules.analysis.schemas import CallSite, ExtractionResult, SourceSpan

VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def url_pattern(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if not isinstance(node, ast.JoinedStr):
        raise ValueError("dynamic_url")
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif (
            isinstance(value, ast.FormattedValue)
            and isinstance(value.value, ast.Name)
            and value.conversion == -1
            and value.format_spec is None
        ):
            parts.append("{" + value.value.id + "}")
        else:
            raise ValueError("invalid_template")
    pattern = "".join(parts)
    parsed = urlsplit(pattern)
    if "{" in parsed.netloc or "{" in parsed.query + parsed.fragment:
        raise ValueError("invalid_template")
    for segment in parsed.path.split("/"):
        if ("{" in segment or "}" in segment) and not (
            segment.startswith("{") and segment.endswith("}") and segment[1:-1].isidentifier()
        ):
            raise ValueError("invalid_template")
    return pattern


def extract(data: bytes, file_id: UUID) -> ExtractionResult:
    try:
        tree = ast.parse(data.decode("utf-8"))
    except (SyntaxError, ValueError, RecursionError) as error:
        line = getattr(error, "lineno", None)
        return ExtractionResult(
            file_id=file_id, language="python", errors=[f"Source syntax error at line {line or 1}"]
        )
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def scope(node: ast.AST) -> ast.AST:
        parent = parents.get(node, tree)
        while not isinstance(parent, SCOPES):
            parent = parents.get(parent, tree)
        return parent

    bindings: dict[ast.AST, dict[str, list[str]]] = {}
    aliases: set[str] = set()
    imported_verbs: dict[str, str] = {}
    sessions: set[str] = set()

    def bind(owner: ast.AST, name: str, kind: str) -> None:
        bindings.setdefault(owner, {}).setdefault(name, []).append(kind)

    name: str | None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                kind = "requests" if alias.name == "requests" and not alias.asname else "other"
                bind(scope(node), name, kind)
                if alias.name == "requests":
                    aliases.add(name)
        elif isinstance(node, ast.ImportFrom) and node.module == "requests":
            for alias in node.names:
                name = alias.asname or alias.name
                bind(scope(node), name, "other")
                if alias.name in VERBS:
                    imported_verbs[name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bind(scope(node), alias.asname or alias.name, "other")
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bind(scope(node), node.name, "other")
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bind(scope(node), node.name, "other")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bind(scope(node), node.id, "other")
        elif isinstance(node, ast.arg):
            bind(scope(node), node.arg, "other")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bind(scope(node), node.name, "other")
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "Session"
        ):
            sessions.update(target.id for target in node.targets if isinstance(target, ast.Name))
    offsets = [0]
    for line in data.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = None
        verb: str | None = None
        reason = "unmapped"
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            name = node.func.value.id
            if name not in aliases | sessions | {"requests"}:
                continue
            if node.func.attr in VERBS:
                verb = node.func.attr
            elif name in aliases | {"requests"} and node.func.attr == "request":
                reason = "unsupported_wrapper"
            else:
                continue
            if name != "requests":
                reason = "unsupported_wrapper" if name in sessions else "unsupported_alias"
            else:
                current = scope(node)
                resolved = False
                while True:
                    kinds = bindings.get(current, {}).get("requests")
                    if kinds:
                        resolved = True
                        if any(kind != "requests" for kind in kinds):
                            reason = "shadowed_requests"
                        break
                    if current is tree:
                        break
                    current = scope(current)
                if not resolved:
                    reason = "requests_import_required"
        elif isinstance(node.func, ast.Name) and node.func.id in imported_verbs:
            verb = imported_verbs[node.func.id]
            reason = "unsupported_alias"
        else:
            continue
        pattern = None
        try:
            argument = (
                node.args[0]
                if node.args
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "url"), None
                )
            )
            if argument is None:
                raise ValueError("missing_url")
            pattern = url_pattern(argument)
            if len(pattern) > 2048:
                pattern = None
                raise ValueError("url_pattern_limit")
        except ValueError as error:
            if reason == "unmapped":
                reason = str(error)
        start = offsets[node.lineno - 1] + node.col_offset
        end_line = node.end_lineno or node.lineno
        end = offsets[end_line - 1] + (node.end_col_offset or node.col_offset)
        calls.append(
            CallSite(
                id="call_" + uuid5(file_id, f"{start}:{end}").hex,
                file_id=file_id,
                language="python",
                span=SourceSpan(
                    byte_start=start, byte_end=end, line_start=node.lineno, line_end=end_line
                ),
                method=verb.upper() if verb else None,
                url_pattern=pattern,
                unresolved_reason=reason,
            )
        )
    return ExtractionResult(
        file_id=file_id,
        language="python",
        callsites=sorted(calls, key=lambda call: call.span.byte_start),
        limitations=["Unaliased requests direct verbs only; no wrappers or value propagation"],
    )
