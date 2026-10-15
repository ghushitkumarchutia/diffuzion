import io
import json
from pathlib import Path
from uuid import uuid4

import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from app.core.config import Settings
from app.core.errors import DomainError
from app.integrations.storage import Storage, new_key
from app.modules.analysis.parser_process import deny_network, run_parser
from app.modules.analysis.schemas import ParserLimits, ParserTask
from app.modules.analysis.specs import parse_spec, resolve_pointer
from tests.conftest import ROOT


def settings(**changes: object) -> Settings:
    values = dict(
        public_origin="http://localhost:3100",
        auth_database_url="postgresql://a:b@localhost/db",
        app_database_url="postgresql+psycopg://a:b@localhost/db",
        gemini_api_key="test",
        razorpay_key_id="rzp_test_example",
        razorpay_key_secret="test",
        razorpay_webhook_secret="test",
    )
    values.update(changes)
    return Settings.model_validate(values)


def document() -> dict:
    return json.loads((ROOT / "evals/cases/development.jsonl").read_text().splitlines()[0])[
        "old_spec"
    ]


def test_valid_internal_refs_and_escaped_pointers() -> None:
    spec = parse_spec(
        (ROOT / "samples/migration-demo/openapi-old.yaml").read_bytes(), uuid4(), ParserLimits()
    )
    assert set(spec.operations) == {"POST /users", "GET /users/{id}", "GET /legacy"}
    assert resolve_pointer(spec.document, "/paths/~1users/post")["responses"]
    assert resolve_pointer({"a/b~c": 1}, "/a~1b~0c") == 1


@pytest.mark.parametrize(
    "data",
    [
        b"{",
        b'{"a":1,"a":2}',
        b"a: 1\na: 2",
        b"a: &x [1]\nb: *x",
        b"a: 1\n---\nb: 2",
        b"a: !!python/object:os.system {}",
        b"openapi: 3.1.0",
        b"\xff",
        b'{"a":NaN}',
        b"[" * 1000,
    ],
)
def test_unsafe_specs_rejected(data: bytes) -> None:
    with pytest.raises(DomainError):
        parse_spec(data, uuid4(), ParserLimits())


@pytest.mark.parametrize(
    "reference",
    ["https://example.com/schema", "file:///tmp/x", "other.yaml#/a", "#/missing", "#/paths/~2"],
)
def test_refs_rejected_before_validator(reference: str, monkeypatch: pytest.MonkeyPatch) -> None:
    value = document()
    value["components"] = {"schemas": {"X": {"$ref": reference}}}
    monkeypatch.setattr(
        "app.modules.analysis.specs.OpenAPIV30SpecValidator",
        lambda _: pytest.fail("unsafe ref reached validator"),
    )
    with pytest.raises(DomainError):
        parse_spec(json.dumps(value).encode(), uuid4(), ParserLimits())


def test_caps_and_dynamic_server() -> None:
    value = document()
    with pytest.raises(DomainError):
        parse_spec(json.dumps(value).encode(), uuid4(), ParserLimits(max_spec_bytes=10))
    value["paths"] = {
        f"/p{i}": {"get": {"responses": {"200": {"description": "ok"}}}} for i in range(101)
    }
    with pytest.raises(DomainError, match="operation limit"):
        parse_spec(json.dumps(value).encode(), uuid4(), ParserLimits())
    value = document()
    value["servers"] = [{"url": "https://{tenant}.example.com"}]
    with pytest.raises(DomainError):
        parse_spec(json.dumps(value).encode(), uuid4(), ParserLimits())
    spec = parse_spec(
        json.dumps(value).encode(),
        uuid4(),
        ParserLimits(),
        api_base_url_override="https://api.example.com/v1",
    )
    assert spec.api_base_url == "https://api.example.com/v1"
    with pytest.raises(DomainError):
        parse_spec(b"a: " + b"[" * 60 + b"0" + b"]" * 60, uuid4(), ParserLimits())
    with pytest.raises(DomainError):
        parse_spec(json.dumps(document()).encode(), uuid4(), ParserLimits(yaml_max_nodes=3))


def test_child_isolation_dispatch_and_limits() -> None:
    task = ParserTask(
        kind="source",
        file_id=uuid4(),
        filename="example.py",
        content='import requests\nrequests.get("/users")\n',
    )
    result = run_parser(task, settings())
    assert result.source and len(result.source.callsites) == 1
    result = run_parser(task.model_copy(update={"filename": "example.java"}), settings())
    assert result.error and result.error.code == "unsupported_language"
    result = run_parser(task, settings(max_code_bytes=4))
    assert result.error and result.error.code == "input_too_large"
    with pytest.raises(PermissionError):
        deny_network("socket.connect", ())
    result = run_parser(
        ParserTask(
            kind="spec",
            file_id=uuid4(),
            filename="old.yaml",
            content=(ROOT / "samples/migration-demo/openapi-old.yaml").read_text(),
        ),
        settings(),
    )
    assert result.spec and result.spec.operations


def test_private_local_storage(tmp_path: Path) -> None:
    storage = Storage(settings(local_storage_path=tmp_path / "private"))
    key = new_key()
    stored = storage.put(key, io.BytesIO(b"data"), max_bytes=4)
    assert stored.size_bytes == 4 and len(stored.sha256) == 64
    assert (tmp_path / "private" / key).stat().st_mode & 0o777 == 0o600
    output = io.BytesIO()
    assert storage.get(key, output, max_bytes=4) == 4 and output.getvalue() == b"data"
    with pytest.raises(DomainError):
        storage.put(key, io.BytesIO(b"other"), max_bytes=10)
    with pytest.raises(DomainError):
        storage.get(key, io.BytesIO(), max_bytes=3)
    for invalid in ("../escape", "/tmp/escape", "normal.yaml"):
        with pytest.raises(DomainError):
            storage.delete(invalid)
    with pytest.raises(DomainError):
        storage.put(new_key(), io.BytesIO(b"too large"), max_bytes=3)
    storage.delete(key)
    storage.delete(key)
    with pytest.raises(DomainError):
        storage.get(key, io.BytesIO(), max_bytes=4)
    key = new_key()
    (storage.root / key).symlink_to(tmp_path / "outside")
    with pytest.raises(DomainError):
        storage.get(key, io.BytesIO(), max_bytes=4)


def test_private_s3_streaming() -> None:
    storage = Storage(
        settings(
            storage_backend="s3",
            bucket_name="private-test",
            bucket_region="ap-south-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
    )
    assert storage.client is not None
    key = new_key()
    with Stubber(storage.client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "private-test",
                "Key": key,
                "Body": ANY,
                "ContentLength": 4,
                "ContentType": "application/octet-stream",
                "IfNoneMatch": "*",
            },
        )
        storage.put(key, io.BytesIO(b"data"), max_bytes=4)
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"data"), 4)},
            {"Bucket": "private-test", "Key": key},
        )
        target = io.BytesIO()
        assert storage.get(key, target, max_bytes=4) == 4 and target.getvalue() == b"data"
        stub.add_response("delete_object", {}, {"Bucket": "private-test", "Key": key})
        storage.delete(key)
        stub.assert_no_pending_responses()


def test_child_never_executes_uploaded_code(tmp_path: Path) -> None:
    target = tmp_path / "must-not-exist"
    source = f'import pathlib\npathlib.Path({str(target)!r}).write_text("executed")\n'
    result = run_parser(
        ParserTask(kind="source", file_id=uuid4(), filename="attack.py", content=source), settings()
    )
    assert result.source is not None and not target.exists()


def test_path_item_reference_and_cap_before_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    value = document()
    value["paths"]["/alias"] = {"$ref": "#/paths/~1users"}
    spec = parse_spec(json.dumps(value).encode(), uuid4(), ParserLimits())
    assert spec.operations["POST /alias"].json_pointer == "/paths/~1users/post"
    monkeypatch.setattr(
        "app.modules.analysis.specs.OpenAPIV30SpecValidator",
        lambda _: pytest.fail("operation cap reached validator"),
    )
    with pytest.raises(DomainError):
        parse_spec(json.dumps(value).encode(), uuid4(), ParserLimits(max_spec_operations=1))


def test_s3_missing_and_immutable_errors_are_safe() -> None:
    storage = Storage(
        settings(
            storage_backend="s3",
            bucket_name="private-test",
            bucket_region="ap-south-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
    )
    assert storage.client is not None
    with Stubber(storage.client) as stub:
        stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
        with pytest.raises(DomainError) as error:
            storage.get(new_key(), io.BytesIO(), max_bytes=4)
        assert error.value.status_code == 404
        stub.add_client_error(
            "put_object", service_error_code="PreconditionFailed", http_status_code=412
        )
        with pytest.raises(DomainError) as error:
            storage.put(new_key(), io.BytesIO(b"data"), max_bytes=4)
        assert error.value.status_code == 409
