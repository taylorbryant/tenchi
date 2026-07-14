"""Test helpers: in-process clients that run the app's lifespan.

``httpx.ASGITransport`` alone never triggers ASGI lifespan events, so apps
using ``create_app(lifespan=...)`` need their startup and shutdown driven
explicitly in tests. These helpers do that internally:

    from tenchi.testing import open_client, open_http

    async with open_client(app) as client:          # typed Client
        todo = await client.call(create_todo_contract, request=...)

    async with open_http(app) as http:              # raw httpx
        response = await http.get("/todos")

Both enter the app lifespan on entry and exit it on exit; apps without a
lifespan work unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx

from .client import Client
from .errors import ErrorDef

ASGIApp = Callable[..., Awaitable[None]]

_DEFAULT_BASE_URL = "http://testserver"

_LIFESPAN_TIMEOUT = 30.0
"""Ceiling on each lifespan phase, so a stuck app fails the test with a
diagnostic instead of hanging the whole suite."""


@asynccontextmanager
async def open_client(
    app: ASGIApp,
    *,
    headers: Mapping[str, str] | None = None,
    errors: Sequence[ErrorDef] = (),
    base_url: str = _DEFAULT_BASE_URL,
) -> AsyncGenerator[Client]:
    """A typed :class:`~tenchi.client.Client` calling ``app`` in-process,
    with the app lifespan running around it."""
    async with (
        _run_lifespan(app),
        Client(
            transport=httpx.ASGITransport(app=app),
            base_url=base_url,
            headers=headers,
            errors=errors,
        ) as client,
    ):
        yield client


@asynccontextmanager
async def open_http(
    app: ASGIApp,
    *,
    headers: Mapping[str, str] | None = None,
    base_url: str = _DEFAULT_BASE_URL,
) -> AsyncGenerator[httpx.AsyncClient]:
    """A raw ``httpx.AsyncClient`` calling ``app`` in-process, with the app
    lifespan running around it. Use this to assert on raw status codes,
    headers, and error envelopes."""
    async with (
        _run_lifespan(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=base_url,
            headers=dict(headers) if headers else None,
        ) as http,
    ):
        yield http


@asynccontextmanager
async def _run_lifespan(app: ASGIApp) -> AsyncGenerator[None]:
    """Drive the ASGI lifespan protocol around the managed block."""
    receive_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    startup_complete = asyncio.Event()
    shutdown_complete = asyncio.Event()
    startup_failure: list[str] = []

    async def receive() -> dict[str, Any]:
        return await receive_queue.get()

    async def send(message: Mapping[str, Any]) -> None:
        kind = message["type"]
        if kind == "lifespan.startup.complete":
            startup_complete.set()
        elif kind == "lifespan.startup.failed":
            startup_failure.append(str(message.get("message", "")))
            startup_complete.set()
        elif kind in ("lifespan.shutdown.complete", "lifespan.shutdown.failed"):
            shutdown_complete.set()

    scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}
    runner = asyncio.ensure_future(app(scope, receive, send))

    async def wait_for(event: asyncio.Event, phase: str) -> None:
        waiter = asyncio.ensure_future(event.wait())
        done, _ = await asyncio.wait(
            {runner, waiter},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=_LIFESPAN_TIMEOUT,
        )
        if not done:
            waiter.cancel()
            runner.cancel()
            raise RuntimeError(
                f"ASGI app did not answer lifespan {phase} within "
                f"{_LIFESPAN_TIMEOUT:g}s"
            )
        if runner in done and not event.is_set():
            waiter.cancel()
            exception = runner.exception()
            if exception is not None:
                raise exception
            raise RuntimeError("ASGI app exited during lifespan handling")

    await receive_queue.put({"type": "lifespan.startup"})
    await wait_for(startup_complete, "startup")
    if startup_failure:
        # The app announced failure and (per the spec) re-raises; retrieve
        # the runner's exception so the original traceback is chained
        # instead of logged as "Task exception was never retrieved".
        done, _ = await asyncio.wait({runner}, timeout=_LIFESPAN_TIMEOUT)
        cause = runner.exception() if done else None
        if not done:
            runner.cancel()
        raise RuntimeError(
            f"Application startup failed: {startup_failure[0]}"
        ) from cause

    try:
        yield
    finally:
        await receive_queue.put({"type": "lifespan.shutdown"})
        await wait_for(shutdown_complete, "shutdown")
        await asyncio.wait_for(runner, _LIFESPAN_TIMEOUT)
