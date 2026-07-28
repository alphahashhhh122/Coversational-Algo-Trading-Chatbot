"""Progress reporting for long runs.

A deep-research loop takes ten seconds or more. Without feedback that is an
indistinguishable-from-broken silence, so the work needs to say where it has
got to while it is still running.

**Why a context variable and not a parameter.** The work that knows about
progress (a LangGraph node, a committee member returning) sits several layers
below the endpoint that wants to stream it — through the agent kernel, the tool
registry, and a Pydantic-validated handler. Threading a callback through all of
that means changing the signature of every tool handler for something that is
pure observability. A context variable keeps those signatures untouched, and
keeps progress genuinely optional: with no sink installed, ``report`` is a
no-op and the code path is exactly what it was before.

**Reporting can never break the work.** A failing sink is swallowed. Losing a
progress line is a cosmetic problem; letting it abort a research run would mean
observability had changed behaviour, which is the one thing it must not do.

Context variables do not cross into new threads by themselves, which is
deliberate here rather than awkward: the worker thread that runs the agent
installs the sink itself, so a stream only ever receives progress from its own
run and never from a concurrent one.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Callable, Iterator

_sink: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = (
    contextvars.ContextVar("progress_sink", default=None)
)


@contextmanager
def reporting_to(sink: Callable[[dict[str, Any]], None]) -> Iterator[None]:
    """Install ``sink`` for the duration of the block."""
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def report(step: str, message: str, **detail: Any) -> None:
    """Note that the run has reached ``step``. A no-op when nobody is listening."""
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink({"step": step, "message": message, "detail": detail or {}})
    except Exception:  # noqa: BLE001 - observability must not change behaviour
        pass


def is_reporting() -> bool:
    """Whether a sink is installed. For skipping expensive progress detail."""
    return _sink.get() is not None
