from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class DomainError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.retry_after = retry_after


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    retry_after: int | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", None),
    }
    if details is not None:
        body["details"] = details
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse({"error": body}, status_code=status, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    async def domain_handler(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, DomainError)
        return error_response(
            request, exc.status_code, exc.code, exc.message, exc.details, exc.retry_after
        )

    async def validation_handler(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        fields = [
            {
                "location": [str(part)[:80] for part in item["loc"][:6]],
                "type": str(item["type"])[:80],
            }
            for item in exc.errors()[:10]
        ]
        return error_response(
            request, 422, "invalid_input", "Request validation failed", {"fields": fields}
        )

    async def http_handler(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, HTTPException)
        messages = {
            401: ("unauthenticated", "Sign in is required"),
            403: ("forbidden", "Access is forbidden"),
            404: ("not_found", "Resource not found"),
            409: ("conflict", "Request conflicts with current state"),
            413: ("input_too_large", "Input exceeds the allowed size"),
            422: ("invalid_input", "Input is invalid or unsupported"),
            429: ("rate_limited", "Too many requests"),
            503: ("unavailable", "Service is temporarily unavailable"),
        }
        code, message = messages.get(exc.status_code, ("request_failed", "Request failed"))
        return error_response(request, exc.status_code, code, message)

    async def unexpected_handler(request: Request, exc: Exception) -> JSONResponse:
        return error_response(request, 500, "internal_error", "An internal error occurred")

    app.add_exception_handler(DomainError, domain_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(HTTPException, http_handler)
    app.add_exception_handler(Exception, unexpected_handler)
