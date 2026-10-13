import json
import logging
import re
from time import perf_counter
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
logger = logging.getLogger("diffuzion")


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "event": record.msg}
        for key in (
            "request_id",
            "method",
            "status",
            "duration_ms",
            "stage",
            "model",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "error_code",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        values = [v.decode("latin-1") for k, v in scope["headers"] if k.lower() == b"x-request-id"]
        candidate = values[0] if len(values) == 1 else ""
        request_id = candidate if REQUEST_ID.fullmatch(candidate) else uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        status = 500
        start = perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            logger.info(
                "request_completed",
                extra={
                    "request_id": request_id,
                    "method": scope["method"],
                    "status": status,
                    "duration_ms": round((perf_counter() - start) * 1000, 2),
                },
            )
