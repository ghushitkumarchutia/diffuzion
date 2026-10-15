import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.errors import DomainError
from app.core.subprocesses import ProcessFailure, ProcessResult
from app.modules.analysis.diff import compare_specs, normalize
from app.modules.analysis.schemas import ParserLimits
from app.modules.analysis.specs import parse_spec, resolve_pointer
from tests.conftest import ROOT
from tests.unit.test_specs import settings


def specs():
    return tuple(
        parse_spec(
            (ROOT / f"samples/migration-demo/openapi-{role}.yaml").read_bytes(),
            uuid4(),
            ParserLimits(),
            role="old_spec" if role == "old" else "new_spec",
        )
        for role in ("old", "new")
    )


def test_real_pinned_engine() -> None:
    executable = os.environ.get("OASDIFF_PATH")
    if not executable:
        pytest.fail("OASDIFF_PATH must point to the installed pinned engine for Day 3 tests")
    old, new = specs()
    result = compare_specs(old, new, settings(oasdiff_path=Path(executable)))
    assert result.engine_version == "1.33.0"
    assert {finding.change_type for finding in result.findings} >= {
        "api-path-removed-without-deprecation",
        "new-required-request-property",
        "request-property-enum-value-removed",
        "response-optional-property-removed",
    }
    assert all(
        f.severity == "breaking"
        for f in result.findings
        if f.change_type != "response-optional-property-removed"
    )
    for finding in result.findings:
        for spec, location in [(old, finding.old_location), (new, finding.new_location)]:
            assert location
            resolve_pointer(spec.document, location.json_pointer)
    empty = compare_specs(old, old, settings(oasdiff_path=Path(executable)))
    assert not empty.findings and empty.message == "No detected consumer-facing contract changes"
    assert json.loads(empty.raw_output) == []


def test_global_and_operation_fallback() -> None:
    old, new = specs()
    result = normalize(
        json.dumps([{"id": "global", "text": "Global contract change", "level": 2}]), old, new
    )
    assert result.findings[0].operation_key is None
    assert result.findings[0].severity == "breaking"
    assert result.findings[0].old_location.precision == "document"


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        "null",
        "[{}]",
        '[{"id":"x","text":"x","level":4}]',
        '[{"id":"x","text":"x","level":1,"invented":true}]',
    ],
)
def test_invalid_shape(raw: str) -> None:
    with pytest.raises(DomainError):
        normalize(raw, *specs())


@pytest.mark.parametrize("code", ["parser_failed", "parser_output_limit", "parser_timeout"])
def test_bounded_failures(code: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args, **kwargs):
        raise ProcessFailure(code)

    monkeypatch.setattr("app.modules.analysis.diff.run_bounded", fail)
    with pytest.raises(ProcessFailure):
        compare_specs(*specs(), settings())


def test_wrong_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.analysis.diff.run_bounded",
        lambda *args, **kwargs: ProcessResult(b"oasdiff version 0", b""),
    )
    with pytest.raises(DomainError, match="pinned"):
        compare_specs(*specs(), settings())


def test_development_contract_changes_match_independent_labels() -> None:
    cases = [
        json.loads(line)
        for line in (ROOT / "evals/cases/development.jsonl").read_text().splitlines()
    ]
    labels = [
        json.loads(line)
        for line in (ROOT / "evals/labels/development.jsonl").read_text().splitlines()
    ]
    executable = Path(os.environ["OASDIFF_PATH"])
    for case, label in zip(cases, labels, strict=True):
        old = parse_spec(json.dumps(case["old_spec"]).encode(), uuid4(), ParserLimits())
        new = parse_spec(
            json.dumps(case["new_spec"]).encode(), uuid4(), ParserLimits(), role="new_spec"
        )
        result = compare_specs(old, new, settings(oasdiff_path=executable))
        expected = label["expected_change_scope"]
        if expected in {"unchanged", "documentation_only"}:
            assert not result.findings, case["family_id"]
        elif expected == "breaking":
            assert any(finding.severity == "breaking" for finding in result.findings), case[
                "family_id"
            ]
        else:
            assert result.findings and all(
                finding.severity == "nonbreaking" for finding in result.findings
            ), case["family_id"]


def test_installer_fails_on_checksum_mismatch(tmp_path: Path) -> None:
    import subprocess

    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        'for argument do destination=$argument; done\nprintf corrupt > "$destination"\n'
    )
    fake_curl.chmod(0o755)
    destination = tmp_path / "engine"
    result = subprocess.run(
        ["sh", str(ROOT / "infra/docker/install-oasdiff.sh"), str(destination)],
        env={"PATH": f"{tmp_path}:{os.defpath}"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0 and "checksum mismatch" in result.stderr
    assert not destination.exists()


def test_real_engine_output_cap() -> None:
    old, new = specs()
    document = json.loads(json.dumps(old.document))
    for index in range(20):
        document["paths"][f"/removed-{index}"] = {
            "get": {"responses": {"200": {"description": "Success"}}}
        }
    old = parse_spec(json.dumps(document).encode(), old.file_id, ParserLimits())
    with pytest.raises(ProcessFailure) as error:
        compare_specs(
            old,
            new,
            settings(oasdiff_path=Path(os.environ["OASDIFF_PATH"]), parser_output_bytes=1024),
        )
    assert error.value.code == "parser_output_limit"
