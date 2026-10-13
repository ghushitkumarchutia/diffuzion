import math
from collections import deque
from threading import Lock
from time import monotonic
from typing import Literal

from app.core.config import Settings
from app.core.errors import DomainError

RateCategory = Literal["read", "upload", "start", "payment", "ip", "auth"]


class RateLimiter:
    def __init__(self, settings: Settings) -> None:
        self.limits = {
            "read": settings.reads_per_minute,
            "upload": settings.uploads_per_minute,
            "start": settings.starts_per_minute,
            "payment": settings.payment_checks_per_minute,
            "ip": settings.ip_requests_per_minute,
            "auth": settings.auth_requests_per_minute,
        }
        self.max_entries = settings.rate_limit_max_entries
        self.entries: dict[tuple[RateCategory, str], deque[float]] = {}
        self.lock = Lock()

    def check(self, category: RateCategory, identity: str) -> None:
        if not identity or len(identity) > 256:
            raise ValueError("Rate limit identity must be bounded and nonempty")
        with self.lock:
            now = monotonic()
            for key in list(self.entries):
                window = self.entries[key]
                while window and window[0] <= now - 60:
                    window.popleft()
                if not window:
                    del self.entries[key]
            key = (category, identity)
            if key not in self.entries and len(self.entries) >= self.max_entries:
                raise DomainError(
                    429, "rate_limited", "Rate limit capacity reached", retry_after=60
                )
            window = self.entries.setdefault(key, deque())
            if len(window) >= self.limits[category]:
                retry_after = max(1, math.ceil(60 - (now - window[0])))
                raise DomainError(429, "rate_limited", "Too many requests", retry_after=retry_after)
            window.append(now)
