"""Database preparation at startup, kept off the server's critical path.

The container has to start listening on its port quickly: Cloudflare gives up on a
container that isn't accepting connections within its startup window ("The container
is not listening in the TCP address 10.0.0.1:8000"). Migrations and seeding need the
database, and the first connection to a Neon branch that has been archived for
inactivity waits for Neon to restore it, which can take longer than that window.

So the server starts listening at once and the database is prepared in the background,
retrying until it succeeds. Until then, API requests wait for it (up to
GATE_WAIT_SECONDS, so an ordinary cold start still just looks slow) and then get a
503 with Retry-After; the health check always answers and reports the state.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("langkey.startup")

# How long an API request waits for the database before getting a 503.
GATE_WAIT_SECONDS = 25.0
HEALTH_PATH = "/api/v1/health"


class Readiness:
    """Whether the database has been migrated and seeded.

    Ready by default: only the app's lifespan (a real server start) marks it as
    starting, so tests that drive the app without a lifespan are unaffected.
    """

    # A plain flag, polled while waiting, rather than an asyncio.Event: an Event binds
    # to the first event loop that waits on it, and this object lives at module level.
    POLL_SECONDS = 0.1

    def __init__(self) -> None:
        self._ready = True
        self.attempts = 0
        self.error: str | None = None

    @property
    def status(self) -> str:
        return "ready" if self._ready else "starting"

    def mark_starting(self) -> None:
        self._ready = False
        self.attempts = 0
        self.error = None

    def mark_ready(self) -> None:
        self.error = None
        self._ready = True

    async def wait(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds; True if the database became ready."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self._ready:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(self.POLL_SECONDS, remaining))
        return True


readiness = Readiness()


async def prepare_database(
    steps: Sequence[Callable[[], Awaitable[None]]],
    *,
    first_delay: float = 1.0,
    max_delay: float = 30.0,
) -> None:
    """Run the startup steps in order, retrying all of them with backoff until they
    succeed. The steps (migrations, seeding, backfills) are idempotent, so repeating
    ones that already succeeded is safe."""
    delay = first_delay
    while True:
        readiness.attempts += 1
        try:
            for step in steps:
                await step()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure means "not ready yet"
            readiness.error = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning(
                "Database not ready (attempt %d): %s. Retrying in %.0fs.",
                readiness.attempts, readiness.error, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
        else:
            readiness.mark_ready()
            logger.info("Database ready after %d attempt(s).", readiness.attempts)
            return


class StartupGateMiddleware:
    """Hold API requests until the database is ready; after GATE_WAIT_SECONDS, answer
    503 with Retry-After. The health check and non-API paths pass straight through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] == "http"
            and path.startswith("/api/")
            and path != HEALTH_PATH
            and not await readiness.wait(GATE_WAIT_SECONDS)
        ):
            response = JSONResponse(
                {"detail": "LangKey is starting up. Try again in a few seconds."},
                status_code=503,
                headers={"Retry-After": "5"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
