from ipaddress import ip_address

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.errors import DomainError, error_response

WEBHOOK_PATH = "/api/v1/billing/webhooks/razorpay"


def require_same_origin(request: Request, settings: Settings) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.method == "POST" and request.url.path == WEBHOOK_PATH:
        return
    if request.headers.getlist("origin") != [settings.public_origin] or request.headers.getlist(
        "x-diffuzion-request"
    ) != ["1"]:
        raise DomainError(403, "untrusted_origin", "Request origin is not allowed")


def client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else "unknown"
    trusted = {host.strip() for host in settings.trusted_proxy_hosts.split(",") if host.strip()}
    if peer not in trusted:
        return peer
    forwarded = request.headers.getlist("x-forwarded-for")
    if len(forwarded) != 1:
        return peer
    try:
        return str(ip_address(forwarded[0].split(",")[-1].strip()))
    except ValueError:
        return peer


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"same-origin"),
                        (b"x-frame-options", b"DENY"),
                        (b"cache-control", b"no-store"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        try:
            require_same_origin(request, self.settings)
        except DomainError as exc:
            response = error_response(request, exc.status_code, exc.code, exc.message)
            await response(scope, receive, secure_send)
            return

        await self.app(scope, receive, secure_send)
