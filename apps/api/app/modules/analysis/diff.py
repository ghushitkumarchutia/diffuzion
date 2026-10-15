import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid5

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.subprocesses import run_bounded
from app.modules.analysis.schemas import ContractLocation, Finding, ParsedSpec, ParserLimits
from app.modules.analysis.specs import parse_spec


class EngineSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str | None = None
    line: int | None = None
    column: int | None = None
    endLine: int | None = None
    endColumn: int | None = None


class EngineChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4000)
    level: int = Field(ge=1, le=3, strict=True)
    operation: str | None = None
    path: str | None = None
    operationId: str | None = None
    comment: str | None = None
    disclaimers: list[str] | None = None
    section: str | None = None
    attributes: dict[str, Any] | None = None
    baseSource: EngineSource | None = None
    revisionSource: EngineSource | None = None
    fingerprint: str | None = None


class DiffResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine_version: str
    raw_output: str
    findings: list[Finding]
    message: str


def location(spec: ParsedSpec, key: str | None) -> ContractLocation:
    operation = spec.operations.get(key) if key else None
    return ContractLocation(
        file_id=spec.file_id,
        json_pointer=operation.json_pointer if operation else "",
        precision="operation" if operation else "document",
    )


def normalize(raw: str, old: ParsedSpec, new: ParsedSpec) -> DiffResult:
    try:
        changes = TypeAdapter(list[EngineChange]).validate_json(raw)
    except ValidationError:
        raise DomainError(
            503, "diff_invalid_output", "Comparison engine output is invalid"
        ) from None
    findings = []
    for index, change in enumerate(changes):
        key = (
            f"{change.operation.upper()} {change.path}"
            if change.operation and change.path
            else None
        )
        if key not in old.operations and key not in new.operations:
            key = None
        findings.append(
            Finding(
                id="change_"
                + uuid5(
                    old.file_id, f"{new.file_id}:{index}:{change.id}:{change.path}:{change.text}"
                ).hex,
                operation_key=key,
                severity="breaking" if change.level in {2, 3} else "nonbreaking",
                change_type=change.id,
                message=change.text,
                old_location=location(old, key),
                new_location=location(new, key),
            )
        )
    return DiffResult(
        engine_version="1.33.0",
        raw_output=raw,
        findings=findings,
        message="No detected consumer-facing contract changes"
        if not findings
        else "Detected consumer-facing contract changes",
    )


def compare_specs(old: ParsedSpec, new: ParsedSpec, settings: Settings) -> DiffResult:
    limits = ParserLimits.model_validate(
        {key: getattr(settings, key) for key in ParserLimits.model_fields}
    )
    old = parse_spec(
        json.dumps(old.document, ensure_ascii=False).encode(),
        old.file_id,
        limits,
        api_base_url_override=old.api_base_url,
    )
    new = parse_spec(
        json.dumps(new.document, ensure_ascii=False).encode(), new.file_id, limits, role="new_spec"
    )
    executable = settings.oasdiff_path.resolve()
    allowed = frozenset({executable})
    version = run_bounded(
        executable, ["--version"], settings, allowed_executables=allowed, memory_resource="data"
    )
    try:
        installed_version = version.stdout.decode("utf-8").strip()
    except UnicodeError:
        raise DomainError(
            503, "diff_version_mismatch", "The pinned comparison engine is required"
        ) from None
    if installed_version != "oasdiff version 1.33.0":
        raise DomainError(503, "diff_version_mismatch", "The pinned comparison engine is required")
    with tempfile.TemporaryDirectory(prefix="diffuzion-specs-") as directory:
        paths = [Path(directory) / "old.json", Path(directory) / "new.json"]
        for path, spec in zip(paths, [old, new], strict=True):
            path.write_text(json.dumps(spec.document), encoding="utf-8")
            path.chmod(0o600)
        result = run_bounded(
            executable,
            [
                "changelog",
                str(paths[0]),
                str(paths[1]),
                "--format",
                "json",
                "--allow-external-refs=false",
            ],
            settings,
            allowed_executables=allowed,
            memory_resource="data",
        )
    try:
        return normalize(result.stdout.decode("utf-8"), old, new)
    except UnicodeError:
        raise DomainError(
            503, "diff_invalid_output", "Comparison engine output is invalid"
        ) from None
