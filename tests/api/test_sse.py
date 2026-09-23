import json
import threading

import httpx

from aidoc.server.sse import format_event, replay_plan


def test_format():
    assert format_event(7, "task.updated", {"a": "中"}) == 'id: 7\nevent: task.updated\ndata: {"a": "中"}\n\n'


def test_publish_writes_db_then_fanout(ctx):
    bus = ctx.bus
    sub = bus.subscribe()
    seq = bus.publish("task.log", "t1", {"line": "x"})
    assert ctx.store.events_since(seq - 1)[0]["kind"] == "task.log"
    assert sub.queue.get(timeout=1)[0] == seq
    sub.close()
    assert bus.subscriber_count == 0


def test_replay_plan(ctx):
    s = ctx.store
    for i in range(30):
        s.append_event("x", None, {"i": i})
    s.prune_events(keep=10)
    oldest = s.oldest_event_seq()
    assert replay_plan(s, None)[0] == "live"
    assert replay_plan(s, oldest - 1)[0] == "replay" and len(replay_plan(s, oldest - 1)[1]) == 10
    assert replay_plan(s, oldest + 4)[0] == "replay" and len(replay_plan(s, oldest + 4)[1]) == 5
    assert replay_plan(s, oldest - 2)[0] == "resync"


def test_replay_plan_empty_table(ctx):
    assert replay_plan(ctx.store, 0) == ("replay", [])


def test_stream_live_and_replay(live_server, ctx):
    s1 = ctx.bus.publish("job.updated", "j", {"id": "j"})
    with httpx.Client(base_url=live_server, timeout=10) as client, client.stream("GET", "/api/events", headers={"Last-Event-ID": str(s1 - 1)}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        it = r.iter_lines()
        lines = []
        while len(lines) < 3:
            lines.append(next(it))
        assert lines[0] == f"id: {s1}" and lines[1] == "event: job.updated" and json.loads(lines[2][6:])["id"] == "j"
        threading.Timer(0.2, lambda: ctx.bus.publish("task.log", "t", {"line": "hi"})).start()
        more = []
        while len(more) < 3:
            line = next(it)
            if line:
                more.append(line)
        assert more[1] == "event: task.log"


def test_stream_resync(live_server, ctx):
    for _ in range(30):
        ctx.store.append_event("x", None, {})
    ctx.store.prune_events(keep=5)
    with httpx.Client(base_url=live_server, timeout=10) as client, client.stream("GET", "/api/events", headers={"Last-Event-ID": "1"}) as r:
        it = r.iter_lines()
        first = next(line for line in it if line)
        assert first == "event: resync"          # no id: line, so the client's Last-Event-ID does not move


def test_slow_subscriber_overflow_gets_resync(ctx):
    bus = ctx.bus
    sub = bus.subscribe(maxsize=3)
    for i in range(5):
        bus.publish("x", None, {"i": i})
    assert sub.overflowed is True
    sub.close()


def test_stream_ends_subscription_on_disconnect(live_server, ctx):
    import time
    with httpx.Client(base_url=live_server, timeout=10) as client, client.stream("GET", "/api/events") as r:
        assert r.status_code == 200
        deadline = time.time() + 5
        while ctx.bus.subscriber_count == 0 and time.time() < deadline:
            time.sleep(0.05)
        assert ctx.bus.subscriber_count == 1
    deadline = time.time() + 5
    while ctx.bus.subscriber_count and time.time() < deadline:
        time.sleep(0.05)
    assert ctx.bus.subscriber_count == 0
