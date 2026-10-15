from uuid import uuid4

import pytest

from app.modules.analysis.extractors.javascript_typescript import extract


@pytest.mark.parametrize("language", ["javascript", "typescript"])
def test_supported_patterns_and_unicode(language: str) -> None:
    data = (
        'const label="café 🦋";\nfetch("/users?x=1#top");\n'
        'fetch(`/users/${id}` , {method: "post"});'
    ).encode()
    calls = extract(data, uuid4(), language).callsites
    assert [call.method for call in calls] == ["GET", "POST"]
    assert calls[1].url_pattern == "/users/{argument_0}"
    assert calls[0].span.line_start == 2
    assert data[calls[0].span.byte_start : calls[0].span.byte_end] == b'fetch("/users?x=1#top")'


@pytest.mark.parametrize(
    "source,reason",
    [
        ("fetch(url)", "dynamic_url"),
        ('fetch("/users", {method: method})', "dynamic_method"),
        ('fetch("/users", options)', "dynamic_options"),
        ('fetch("/users", {...options})', "dynamic_options"),
        ("fetch(`/users/pre-${id}`)", "invalid_template"),
        ("fetch(`https://${host}/users`)", "invalid_template"),
        ('axios.get("/users")', "unsupported_wrapper"),
        ('function x(fetch) {fetch("/users")}', "shadowed_fetch"),
        ('const fetch = client; fetch("/users")', "shadowed_fetch"),
        ('import fetch from "client";fetch("/users")', "shadowed_fetch"),
        ('fetch("/users", {method: "BAD"})', "unsupported_method"),
    ],
)
def test_unresolved_patterns(source: str, reason: str) -> None:
    result = extract(source.encode(), uuid4(), "javascript")
    assert len(result.callsites) == 1
    assert result.callsites[0].unresolved_reason == reason
    assert result.callsites[0].operation_key is None


def test_syntax_errors_and_scope() -> None:
    result = extract(b'fetch("/users"); const x = ;', uuid4(), "typescript")
    assert result.errors and result.callsites[0].unresolved_reason == "syntax_error"
    calls = extract(
        b'function x(fetch) {fetch("/users")} fetch("/users")', uuid4(), "javascript"
    ).callsites
    assert [call.unresolved_reason for call in calls] == ["shadowed_fetch", "unmapped"]


@pytest.mark.parametrize(
    "source,reason",
    [
        ('try {} catch (fetch) {fetch("/users")}', "shadowed_fetch"),
        ('class fetch {} fetch("/users")', "shadowed_fetch"),
        ('fetch("/users", {get method() {return "POST"}})', "dynamic_method"),
    ],
)
def test_additional_visible_shadowing_and_accessors(source: str, reason: str) -> None:
    assert extract(source.encode(), uuid4(), "javascript").callsites[0].unresolved_reason == reason


def test_import_alias_binding_and_overlong_url() -> None:
    calls = extract(
        b'import {fetch as remoteFetch} from "client";fetch("/users")', uuid4(), "javascript"
    ).callsites
    assert calls[0].unresolved_reason == "unmapped"
    call = extract(('fetch("/' + "x" * 2048 + '")').encode(), uuid4(), "javascript").callsites[0]
    assert call.url_pattern is None and call.unresolved_reason == "url_pattern_limit"


def test_jsx_is_not_automatically_supported_in_js_files() -> None:
    result = extract(b'const view = <div/>; fetch("/users")', uuid4(), "javascript")
    assert result.errors and result.callsites[0].unresolved_reason == "unsupported_jsx"
