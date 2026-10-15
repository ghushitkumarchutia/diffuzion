import json
from uuid import uuid4

import pytest

from app.core.errors import DomainError
from app.modules.analysis.extractors import extract_source
from app.modules.analysis.mapping import apply_manual_mappings, map_calls, path_for_url
from app.modules.analysis.schemas import ManualMapping, ParserLimits
from app.modules.analysis.specs import parse_spec
from tests.conftest import ROOT


def test_all_development_gold_mappings_and_spans() -> None:
    cases = [
        json.loads(line)
        for line in (ROOT / "evals/cases/development.jsonl").read_text().splitlines()
    ]
    labels = {
        row["family_id"]: row
        for row in [
            json.loads(line)
            for line in (ROOT / "evals/labels/development.jsonl").read_text().splitlines()
        ]
    }
    assert len(cases) == len(labels) == 20
    assert sum(case["ai_spike_selected"] for case in cases) == 5
    for case in cases:
        spec = parse_spec(json.dumps(case["old_spec"]).encode(), uuid4(), ParserLimits())
        parse_spec(json.dumps(case["new_spec"]).encode(), uuid4(), ParserLimits(), role="new_spec")
        assert len(case["source_variants"]) == 3 and case["split"] == "development"
        for variant in case["source_variants"]:
            gold_calls = [
                gold
                for gold in labels[case["family_id"]]["mapping_pairs"]
                if gold["variant_id"] == variant["id"]
            ]
            calls = map_calls(
                extract_source(
                    variant["source"].encode(), uuid4(), variant["filename"], ParserLimits()
                ).callsites,
                spec,
            )
            assert len(calls) == len(gold_calls), case["family_id"]
            for call, gold in zip(calls, gold_calls, strict=True):
                assert call.span.model_dump() == gold["span"], (
                    case["family_id"],
                    variant["language"],
                )
                assert call.operation_key == gold["operation_key"], (
                    case["family_id"],
                    variant["language"],
                )
                assert call.unresolved_reason == gold["unresolved_reason"]
                assert call.candidate_operation_keys == gold["candidate_operation_keys"]
        for passage in labels[case["family_id"]]["relevant_passages"]:
            assert (
                case["notes"][0]["content"]
                .encode()[passage["byte_start"] : passage["byte_end"]]
                .decode()
                == passage["text"]
            )


@pytest.mark.parametrize(
    "url,expected", [("https://example.com:443/v1/users?x=1#z", "/users"), ("/users?x=1", "/users")]
)
def test_origin_base_query(url: str, expected: str) -> None:
    assert path_for_url(url, "https://example.com/v1") == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/v1/users",
        "https://example.com/v10/users",
        "https://user:pass@example.com/v1/users",
        "//example.com/users",
        "users",
    ],
)
def test_url_boundaries(url: str) -> None:
    with pytest.raises(ValueError):
        path_for_url(url, "https://example.com/v1")


def test_manual_revision_and_candidates() -> None:
    case = json.loads((ROOT / "evals/cases/development.jsonl").read_text().splitlines()[8])
    spec = parse_spec(json.dumps(case["old_spec"]).encode(), uuid4(), ParserLimits())
    source = case["source_variants"][0]
    file_id = uuid4()
    calls = map_calls(
        extract_source(
            source["source"].encode(), file_id, source["filename"], ParserLimits()
        ).callsites,
        spec,
    )
    ids = {file_id, spec.file_id}
    override = ManualMapping(callsite_id=calls[0].id, operation_key="GET /users/{id}")
    mapped = apply_manual_mappings(calls, [override], spec, ids)
    assert mapped[0].mapping_source == "manual" and mapped[0].unresolved_reason is None
    assert map_calls(mapped, spec) == mapped
    for overrides, files in [
        ([override], {file_id}),
        ([override.model_copy(update={"callsite_id": "call_" + uuid4().hex})], ids),
        ([override.model_copy(update={"operation_key": "GET /renamed"})], ids),
        ([override, override], ids),
    ]:
        with pytest.raises(DomainError):
            apply_manual_mappings(calls, overrides, spec, files)


def test_manual_mapping_cannot_bypass_candidate_checks() -> None:
    case = json.loads((ROOT / "evals/cases/development.jsonl").read_text().splitlines()[0])
    spec = parse_spec(json.dumps(case["old_spec"]).encode(), uuid4(), ParserLimits())
    file_id = uuid4()
    calls = map_calls(
        extract_source(
            b'fetch("https://other.example/users")', file_id, "consumer.js", ParserLimits()
        ).callsites,
        spec,
    )
    override = ManualMapping(callsite_id=calls[0].id, operation_key="POST /users")
    with pytest.raises(DomainError):
        apply_manual_mappings(calls, [override], spec, {file_id, spec.file_id})
