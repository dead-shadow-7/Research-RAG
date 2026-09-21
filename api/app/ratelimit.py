"""Per-user rate limiting for the endpoints that cost money.

Sign-up is open and email confirmation is off, so an account takes seconds to create.
`MAX_DOCUMENTS_PER_USER` bounds what one account can *ingest*, but nothing bounded what
it could *ask*: every question is an embedding call plus generation tokens on a metered
provider. These limits close that.

Deliberately in-process rather than in Redis. The deployed stack is a single container
with no Redis at all, so a shared store would mean adding a dependency to protect
against a case that cannot arise yet. The consequences are worth stating plainly:

* counters reset when the container restarts
* two replicas would each allow the full limit, doubling it in practice

Both are fine for one box and wrong for several. `app/queue.py` already has a Redis
client to borrow when that changes.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from app.auth import current_user_id
from app.config import settings

# Stop the counter dict growing without bound on a long-running process: any key whose
# window has fully expired is dropped on the next sweep.
_SWEEP_EVERY_SECONDS = 300


class SlidingWindow:
    """Allows `limit()` events per `window` seconds, per key.

    A sliding window rather than a fixed one: a fixed window lets someone spend the
    whole budget in the last second of one window and the whole budget again in the
    first second of the next, which is twice the intended rate at the moment it matters.
    """

    def __init__(self, name: str, limit: Callable[[], int], window: float) -> None:
        self.name = name
        self.window = window
        # A callable, not an int, so tests and `.env` overrides are read at check time
        # rather than frozen at import.
        self._limit = limit
        self._hits: dict[uuid.UUID, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._last_sweep = time.monotonic()

    async def check(self, key: uuid.UUID) -> float | None:
        """Record an event. Returns seconds to wait if the caller is over the limit."""
        limit = self._limit()
        if limit <= 0:  # 0 disables the limit rather than blocking everything
            return None

        now = time.monotonic()
        async with self._lock:
            self._sweep(now)
            hits = self._hits[key]
            cutoff = now - self.window
            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= limit:
                return hits[0] + self.window - now

            hits.append(now)
            return None

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < _SWEEP_EVERY_SECONDS:
            return
        self._last_sweep = now
        cutoff = now - self.window
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]

    def reset(self) -> None:
        """For tests. Never called in normal operation."""
        self._hits.clear()


chat_per_minute = SlidingWindow("chat", lambda: settings.chat_per_minute, 60)
chat_per_day = SlidingWindow("chat", lambda: settings.chat_per_day, 24 * 60 * 60)
uploads_per_hour = SlidingWindow("upload", lambda: settings.uploads_per_hour, 60 * 60)


def limited(*windows: SlidingWindow) -> Callable:
    """A dependency that rate-limits the caller and yields their user id.

    It depends on `current_user_id`, which FastAPI resolves once per request, so a route
    can take both this and `current_user_id` without verifying the token twice.
    """

    async def dependency(user_id: uuid.UUID = Depends(current_user_id)) -> uuid.UUID:
        for window in windows:
            retry_after = await window.check(user_id)
            if retry_after is not None:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Rate limit reached for {window.name}. "
                        f"Try again in {math.ceil(retry_after)}s."
                    ),
                    # Clients and proxies both understand this; without it a caller has
                    # no way to know how long to back off and will usually hammer.
                    headers={"Retry-After": str(math.ceil(retry_after))},
                )
        return user_id

    return dependency


def reset_all() -> None:
    for window in (chat_per_minute, chat_per_day, uploads_per_hour):
        window.reset()
