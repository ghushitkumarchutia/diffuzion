import re
from urllib.parse import urlsplit
from uuid import UUID

from app.core.errors import DomainError
from app.modules.analysis.schemas import CallSite, ManualMapping, ParsedSpec


def path_for_url(pattern: str, base_url: str | None) -> str:
    parsed = urlsplit(pattern)
    if parsed.username or parsed.password or any(char.isspace() for char in pattern):
        raise ValueError("unsupported_url")
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or base_url is None:
            raise ValueError("unconfirmed_origin")
        base = urlsplit(base_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        base_port = base.port or (443 if base.scheme == "https" else 80)
        if (parsed.scheme, parsed.hostname, port) != (base.scheme, base.hostname, base_port):
            raise ValueError("different_origin")
        prefix = base.path.rstrip("/")
        if prefix and not (parsed.path == prefix or parsed.path.startswith(prefix + "/")):
            raise ValueError("different_base_path")
        return parsed.path[len(prefix) :] or "/"
    if not pattern.startswith("/") or pattern.startswith("//"):
        raise ValueError("unsupported_relative_url")
    return parsed.path


def path_matches(call_path: str, operation_path: str) -> bool:
    call_segments = call_path.split("/")
    operation_segments = operation_path.split("/")
    if len(call_segments) != len(operation_segments):
        return False
    return all(
        re.fullmatch(r"\{[^{}]+\}", declared) is not None or actual == declared
        for actual, declared in zip(call_segments, operation_segments, strict=True)
    )


def map_calls(calls: list[CallSite], old_spec: ParsedSpec) -> list[CallSite]:
    mapped = []
    for call in calls:
        if call.mapping_source == "manual":
            mapped.append(call)
            continue
        reason = call.unresolved_reason or "unmapped"
        candidates = []
        if call.url_pattern is not None:
            try:
                path = path_for_url(call.url_pattern, old_spec.api_base_url)
                possible = [
                    operation
                    for operation in old_spec.operations.values()
                    if (call.method is None or operation.method == call.method)
                    and path_matches(path, operation.path)
                ]
                exact = [
                    operation
                    for operation in possible
                    if operation.path == path and "{" not in operation.path
                ]
                candidates = sorted(operation.key for operation in (exact or possible))
            except ValueError as error:
                if reason == "unmapped":
                    reason = (
                        str(error)
                        if str(error)
                        in {
                            "unsupported_url",
                            "unconfirmed_origin",
                            "different_origin",
                            "different_base_path",
                            "unsupported_relative_url",
                        }
                        else "unsupported_url"
                    )
        if reason == "unmapped" and call.method is not None and len(candidates) == 1:
            mapped.append(
                call.model_copy(
                    update={
                        "candidate_operation_keys": candidates,
                        "operation_key": candidates[0],
                        "mapping_source": "automatic",
                        "unresolved_reason": None,
                    }
                )
            )
        else:
            if reason == "unmapped":
                reason = "ambiguous_operation" if len(candidates) > 1 else "no_old_operation_match"
            mapped.append(
                call.model_copy(
                    update={
                        "candidate_operation_keys": candidates,
                        "operation_key": None,
                        "mapping_source": None,
                        "unresolved_reason": reason,
                    }
                )
            )
    return mapped


def apply_manual_mappings(
    calls: list[CallSite],
    overrides: list[ManualMapping],
    old_spec: ParsedSpec,
    revision_file_ids: set[UUID],
) -> list[CallSite]:
    if old_spec.file_id not in revision_file_ids or any(
        call.file_id not in revision_file_ids for call in calls
    ):
        raise DomainError(
            422, "invalid_mapping", "Mappings must use files from the selected revision"
        )
    by_id = {call.id: call for call in calls}
    if len(by_id) != len(calls) or len({item.callsite_id for item in overrides}) != len(overrides):
        raise DomainError(422, "invalid_mapping", "Mapping IDs must be unique")
    for item in overrides:
        call = by_id.get(item.callsite_id)
        if call is None or item.operation_key not in old_spec.operations:
            raise DomainError(
                422, "invalid_mapping", "Mapping must reference a detected call and old operation"
            )
        if item.operation_key not in call.candidate_operation_keys:
            raise DomainError(
                422, "invalid_mapping", "Selected operation is not a candidate for this call"
            )
        by_id[item.callsite_id] = call.model_copy(
            update={
                "operation_key": item.operation_key,
                "mapping_source": "manual",
                "unresolved_reason": None,
            }
        )
    return [by_id[call.id] for call in calls]
