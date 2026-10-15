from uuid import uuid4

import pytest

from app.modules.analysis.extractors.python import extract


@pytest.mark.parametrize("verb", ["get", "post", "put", "patch", "delete", "head", "options"])
def test_direct_verbs(verb: str) -> None:
    result = extract(f'import requests\nrequests.{verb}("/users")'.encode(), uuid4())
    assert result.callsites[0].method == verb.upper()
    assert result.callsites[0].unresolved_reason == "unmapped"


def test_templates_and_unicode() -> None:
    data = 'import requests\nlabel="café 🦋"; requests.get(f"/users/{user_id}")'.encode()
    call = extract(data, uuid4()).callsites[0]
    assert call.url_pattern == "/users/{user_id}" and call.span.line_start == 2
    assert data[call.span.byte_start : call.span.byte_end] == b'requests.get(f"/users/{user_id}")'


@pytest.mark.parametrize(
    "source,reason",
    [
        ('import requests as r\nr.get("/users")', "unsupported_alias"),
        ('from requests import get\nget("/users")', "unsupported_alias"),
        ('import requests\nrequests = client\nrequests.get("/users")', "shadowed_requests"),
        ('import requests\ndef f(requests):\n requests.get("/users")', "shadowed_requests"),
        ("import requests\nrequests.get(url)", "dynamic_url"),
        ('import requests\nrequests.get(f"/users/pre-{id}")', "invalid_template"),
        ('import requests\nrequests.get(f"/users/{obj.id}")', "invalid_template"),
        (
            'import requests\nsession=requests.Session()\nsession.get("/users")',
            "unsupported_wrapper",
        ),
        ('requests.get("/users")', "requests_import_required"),
    ],
)
def test_unresolved_patterns(source: str, reason: str) -> None:
    result = extract(source.encode(), uuid4())
    assert len(result.callsites) == 1 and result.callsites[0].unresolved_reason == reason


def test_invalid_syntax() -> None:
    result = extract(b"import requests\nrequests.get(", uuid4())
    assert result.errors and not result.callsites


@pytest.mark.parametrize(
    "source",
    [
        'import requests\nfrom client import requests\nrequests.get("/users")',
        'import requests\ntry:\n pass\nexcept Exception as requests:\n requests.get("/users")',
    ],
)
def test_other_import_and_exception_shadowing(source: str) -> None:
    assert extract(source.encode(), uuid4()).callsites[0].unresolved_reason == "shadowed_requests"


def test_unicode_template_name_and_overlong_url() -> None:
    assert (
        extract('import requests\nrequests.get(f"/users/{用户}")'.encode(), uuid4())
        .callsites[0]
        .url_pattern
        == "/users/{用户}"
    )
    call = extract(
        ('import requests\nrequests.get("/' + "x" * 2048 + '")').encode(), uuid4()
    ).callsites[0]
    assert call.url_pattern is None and call.unresolved_reason == "url_pattern_limit"
