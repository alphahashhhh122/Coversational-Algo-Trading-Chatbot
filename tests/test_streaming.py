from __future__ import annotations

import json
import threading
import time
import unittest

from iimc_trading_platform.api import _agent_run_events, _sse
from iimc_trading_platform.progress import (
    is_reporting,
    report,
    reporting_to,
)


class _Agent:
    name = "stub"


class _Task:
    symbol = "RELIANCE"


def _parse(frames: list[str]) -> list[tuple[str, dict]]:
    """SSE frames -> (event, data), ignoring keep-alive comments."""
    parsed = []
    for frame in frames:
        if frame.startswith(":"):
            continue
        lines = frame.strip().split("\n")
        event = lines[0].removeprefix("event: ")
        parsed.append((event, json.loads(lines[1].removeprefix("data: "))))
    return parsed


class ProgressContextTest(unittest.TestCase):
    def test_reporting_is_a_no_op_when_nobody_listens(self) -> None:
        self.assertFalse(is_reporting())
        report("step", "nothing should happen")  # must not raise

    def test_events_reach_an_installed_sink(self) -> None:
        seen: list[dict] = []
        with reporting_to(seen.append):
            self.assertTrue(is_reporting())
            report("gather", "collecting", count=2)
        self.assertEqual(seen[0]["step"], "gather")
        self.assertEqual(seen[0]["detail"], {"count": 2})
        # The sink is removed again on exit.
        self.assertFalse(is_reporting())

    def test_a_failing_sink_never_breaks_the_work(self) -> None:
        """Observability must not change behaviour."""

        def boom(event):
            raise RuntimeError("sink exploded")

        with reporting_to(boom):
            report("step", "this must return normally")

    def test_the_sink_does_not_leak_into_other_threads(self) -> None:
        """A stream must only see progress from its own run."""
        seen: list[dict] = []
        other_thread_saw_a_sink: list[bool] = []

        def elsewhere() -> None:
            other_thread_saw_a_sink.append(is_reporting())

        with reporting_to(seen.append):
            thread = threading.Thread(target=elsewhere)
            thread.start()
            thread.join()
        self.assertEqual(other_thread_saw_a_sink, [False])


class AgentRunStreamTest(unittest.TestCase):
    def test_progress_is_streamed_then_the_result(self) -> None:
        def run(agent, task):
            report("gather", "collecting the data")
            report("critique", "checking coverage")
            return {"run_id": "arun_1", "status": "ok"}

        events = _parse(list(_agent_run_events(_Agent(), _Task(), run)))
        self.assertEqual([e for e, _ in events], ["started", "progress", "progress", "result"])
        self.assertEqual(events[0][1]["agent"], "stub")
        self.assertEqual(events[1][1]["step"], "gather")
        self.assertEqual(events[-1][1]["run_id"], "arun_1")

    def test_a_run_with_no_progress_still_ends_with_a_result(self) -> None:
        events = _parse(
            list(_agent_run_events(_Agent(), _Task(), lambda a, t: {"status": "ok"}))
        )
        self.assertEqual([e for e, _ in events], ["started", "result"])

    def test_a_failure_is_reported_as_an_event_not_a_dropped_stream(self) -> None:
        def boom(agent, task):
            raise RuntimeError("provider down")

        events = _parse(list(_agent_run_events(_Agent(), _Task(), boom)))
        self.assertEqual(events[-1][0], "failed")
        self.assertIn("provider down", events[-1][1]["error"])

    def test_a_failure_after_progress_keeps_what_was_reported(self) -> None:
        def half(agent, task):
            report("gather", "collecting the data")
            raise RuntimeError("then it broke")

        events = _parse(list(_agent_run_events(_Agent(), _Task(), half)))
        self.assertEqual([e for e, _ in events], ["started", "progress", "failed"])

    def test_a_quiet_run_emits_keep_alives(self) -> None:
        """A silent stretch must not look like a dropped connection."""

        def slow(agent, task):
            time.sleep(0.25)
            return {"status": "ok"}

        frames = list(
            _agent_run_events(_Agent(), _Task(), slow, heartbeat_seconds=0.05)
        )
        self.assertTrue(any(f.startswith(":") for f in frames))
        self.assertEqual([e for e, _ in _parse(frames)], ["started", "result"])

    def test_frames_are_well_formed_sse(self) -> None:
        frame = _sse("progress", {"step": "gather"})
        self.assertTrue(frame.startswith("event: progress\ndata: "))
        self.assertTrue(frame.endswith("\n\n"))


class BodyLimitMiddlewareStreamingTest(unittest.TestCase):
    """Regression: the body-limit middleware must not cancel a stream.

    It buffers the request body and replays it. Replaying used to end with a
    synthesised ``http.disconnect``, which is invisible for an ordinary
    response but tells a streaming response the client left — so the stream was
    cancelled before emitting a single byte, silently and with a 200.
    """

    def _app(self):
        from fastapi import FastAPI
        from fastapi.responses import StreamingResponse

        from iimc_trading_platform.middleware import RequestBodyLimitMiddleware

        app = FastAPI()

        @app.get("/stream")
        def stream():
            def gen():
                yield "event: a\ndata: 1\n\n"
                yield "event: b\ndata: 2\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        @app.post("/echo")
        def echo(body: dict):
            return body

        app.add_middleware(RequestBodyLimitMiddleware, max_bytes=1024)
        return app

    def test_a_stream_survives_the_body_limit_middleware(self) -> None:
        from fastapi.testclient import TestClient

        text = TestClient(self._app()).get("/stream").text
        self.assertIn("event: a", text)
        self.assertIn("event: b", text)

    def test_request_bodies_are_still_replayed_intact(self) -> None:
        """The buffering it exists for must keep working."""
        from fastapi.testclient import TestClient

        response = TestClient(self._app()).post("/echo", json={"hello": "world"})
        self.assertEqual(response.json(), {"hello": "world"})

    def test_oversized_bodies_are_still_rejected(self) -> None:
        from fastapi.testclient import TestClient

        response = TestClient(self._app()).post(
            "/echo",
            content=b'{"x":"' + b"y" * 2048 + b'"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
