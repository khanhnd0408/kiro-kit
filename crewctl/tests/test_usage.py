"""Tests for `crewctl usage`. Every test builds a fake Crew home in a temp folder and points HOME, the settings file and
the state folder there, so nothing here can touch a real ~/.kiro or ~/.config. The only network use is a local HTTP
server on 127.0.0.1 that the tests start themselves.

Run:  python3 -m unittest discover -s tests -v     (from the crewctl folder)
"""

import contextlib
import datetime
import gzip
import http.server
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from crewctl import cli, otlp, usage  # noqa: E402
from crewctl.util import LockError  # noqa: E402

TODAY = datetime.date.today().isoformat()


def ts(minute, day=None):
    day = day or TODAY
    return f"{day}T10:{minute:02d}:00.000000+07:00"


def ms(minute, day=None):
    return int(datetime.datetime.fromisoformat(ts(minute, day)).timestamp() * 1000)


def row(minute, credits, slot="chat-1", agent="orch", model="claude-sonnet-4.5", surface="dashboard", **extra):
    data = {"_type": "tokens", "ts": ts(minute), "slot": slot, "app": "", "provider": "acp", "model": model, "input": 0,
            "output": 0, "cache_create": 0, "cache_read": 0, "cost": 0.0, "credits": credits, "turns": 0, "duration_ms": 1000,
            "surface": surface, "agent": agent, "context_used": 50000, "context_window": 200000, "stop_reason": "end_turn"}
    data.update(extra)
    return data


class World:
    """A fake Crew home, settings file and state folder, with HOME pointed at the temp folder."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.crew = os.path.join(self.root, "crew")
        self.state_dir = os.path.join(self.root, "state")
        self.settings_path = os.path.join(self.root, "usage.json")
        os.makedirs(os.path.join(self.crew, "usage", "tokens"))
        os.makedirs(os.path.join(self.crew, "crew-log", "sessions"))
        self.env = mock.patch.dict(os.environ, {"HOME": os.path.join(self.root, "home"), "CREWCTL_USAGE_CONFIG": self.settings_path,
                                                "CREWCTL_USAGE_STATE": self.state_dir}, clear=False)
        self.env.start()
        for key in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "KIROCREW_HOME", "NO_COLOR"):
            os.environ.pop(key, None)
        self.sent = []
        self.fail = {}

    def close(self):
        self.env.stop()
        self.tmp.cleanup()

    def usage_file(self, day=None):
        return os.path.join(self.crew, "usage", "tokens", f"{day or TODAY}.jsonl")

    def add_rows(self, *rows, day=None, raw_tail=""):
        with open(self.usage_file(day), "a", encoding="utf-8") as handle:
            for item in rows:
                handle.write((item if isinstance(item, str) else json.dumps(item)) + "\n")
            handle.write(raw_tail)

    def session(self, folder, sid, slot, agent="orch", workspace="ops", created_minute=0, entries=()):
        path = os.path.join(self.crew, "crew-log", "sessions", folder)
        os.makedirs(path, exist_ok=True)
        lines = [{"type": "session", "version": 1, "id": sid, "agent": agent, "slot": slot, "cwd": "/secret/client/path",
                  "createdAt": ms(created_minute)},
                 {"type": "session/opened", "seq": 1, "time": ms(created_minute), "data": {"agent": agent, "slot": slot,
                  "cwd": "/secret/client/path", "class": {"memory": "persistent", "workspace": workspace}}}]
        lines += list(entries)
        with open(os.path.join(path, "log.jsonl"), "a", encoding="utf-8") as handle:
            for item in lines:
                handle.write(json.dumps(item) + "\n")
        return path

    def settings(self, **fields):
        data = {"endpoint": "http://localhost:4318", "crew_home": self.crew, "backfill_days": 3}
        data.update(fields)
        with open(self.settings_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        settings, errors, warnings = usage.load_usage_settings(self.settings_path)
        assert not errors, errors
        return settings

    def sender(self, url, payload, headers, timeout):
        kind = "logs" if url.endswith("logs") else "metrics"
        if self.fail.get(kind):
            self.fail[kind] -= 1
            return f"HTTP 503 from {url}"
        self.sent.append((kind, payload))
        return None

    def push(self, settings=None, **kw):
        return usage.run_push(settings or self.settings(), self.state_dir, sender=self.sender, **kw)

    def logs(self):
        return [r for kind, p in self.sent if kind == "logs" for r in otlp.decode_logs(p)]

    def metrics(self):
        return [otlp.decode_metrics(p) for kind, p in self.sent if kind == "metrics"]

    def state(self):
        return usage.load_state(self.state_dir)

    def cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = cli.main(["--no-color"] + list(argv))
        return code, buffer.getvalue()


def series(decoded, name):
    """{frozen labels: last value} of one metric in one decoded request."""
    out = {}
    for metric in decoded:
        if metric["name"] == name:
            for point in metric["points"]:
                out[tuple(sorted(point["attributes"].items()))] = point["value"]
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)


class Settings(Base):
    def load(self, **fields):
        with open(self.w.settings_path, "w", encoding="utf-8") as handle:
            json.dump(fields, handle)
        return usage.load_usage_settings(self.w.settings_path)

    def test_no_file_means_off_and_the_template_is_off_too(self):
        settings, errors, _ = usage.load_usage_settings(self.w.settings_path)
        self.assertEqual(errors, [])
        self.assertFalse(settings.exists or settings.enabled)
        with open(os.path.join(ROOT, "templates", "usage.json"), encoding="utf-8") as handle:
            template = json.load(handle)
        settings, errors, _ = self.load(**template)
        self.assertEqual(errors, [])
        self.assertFalse(settings.enabled)

    def test_endpoint_gives_both_urls_and_full_urls_win(self):
        settings, errors, _ = self.load(endpoint="http://localhost:4318/")
        self.assertEqual((settings.logs_url, settings.metrics_url), ("http://localhost:4318/v1/logs", "http://localhost:4318/v1/metrics"))
        settings, _, _ = self.load(logs_endpoint="https://vl.example:9428/insert/opentelemetry/v1/logs",
                                   metrics_endpoint="https://vm.example:8428/opentelemetry/v1/metrics")
        self.assertTrue(settings.logs_url.endswith("/insert/opentelemetry/v1/logs"))
        settings, _, _ = self.load(endpoint="http://localhost:4318", signals=["metrics"])
        self.assertEqual(settings.logs_url, "")
        self.assertTrue(settings.metrics_url)

    def test_bad_values_are_errors_with_the_allowed_list(self):
        _, errors, _ = self.load(endpoint="ftp://x", colour="red", hash_fields=["prompt"], drop_fields="agent",
                                 signals=["traces"], backfill_days=-1, max_rows_per_request=0)
        text = "\n".join(errors)
        for needle in ("unknown setting 'colour'", "http:// or https://", "hash_fields must be a list of", "drop_fields must be a list of",
                       "signals", "backfill_days", "max_rows_per_request"):
            self.assertIn(needle, text)

    def test_plain_http_to_another_machine(self):
        headers = os.path.join(self.w.root, "h")
        open(headers, "w").close()
        _, errors, _ = self.load(endpoint="http://grafana.internal:4318", headers_file=headers)
        self.assertTrue(any("unencrypted" in e for e in errors))
        _, errors, warnings = self.load(endpoint="http://grafana.internal:4318", headers_file=headers, allow_insecure_http=True)
        self.assertEqual(errors, [])
        _, errors, warnings = self.load(endpoint="http://grafana.internal:4318")
        self.assertEqual(errors, [])
        self.assertTrue(any("unencrypted" in w for w in warnings))
        _, errors, warnings = self.load(endpoint="http://127.0.0.1:4318", headers_file=headers)
        self.assertEqual((errors, warnings), ([], []))

    @unittest.skipIf(os.name == "nt", "file modes")
    def test_headers_file_must_be_private_and_well_formed(self):
        path = os.path.join(self.w.root, "headers")
        with open(path, "w") as handle:
            handle.write("# comment\nAuthorization: Bearer abc:def\n\nX-Scope-OrgID: tenant\n")
        os.chmod(path, 0o644)
        with self.assertRaises(usage.UsageError) as caught:
            usage.read_headers(path)
        self.assertIn("chmod 600", str(caught.exception))
        os.chmod(path, 0o600)
        self.assertEqual(usage.read_headers(path), {"Authorization": "Bearer abc:def", "X-Scope-OrgID": "tenant"})
        with open(path, "a") as handle:
            handle.write("no colon here\n")
        with self.assertRaises(usage.UsageError):
            usage.read_headers(path)


class Reading(Base):
    def test_rows_are_parsed_defensively(self):
        good = usage.parse_usage_row(json.dumps(row(1, 0.5)))
        self.assertEqual((good["credits"], good["agent"], good["context_window"]), (0.5, "orch", 200000))
        self.assertEqual(usage.parse_usage_row(json.dumps({"_type": "other"})), "ignore")
        for bad in ("not json", "[1]", json.dumps({"ts": ts(1), "credits": 1}), json.dumps(row(1, True)), json.dumps(row(1, "1")),
                    json.dumps(dict(row(1, 1.0), ts="yesterday"))):
            self.assertIsNone(usage.parse_usage_row(bad), bad)
        weird = usage.parse_usage_row(json.dumps(dict(row(1, -2.0), duration_ms="x", agent=7, context_used=-1)))
        self.assertEqual((weird["credits"], weird["duration_ms"], weird["agent"], weird["context_used"]), (0.0, 0, "", 0))
        zulu = usage.parse_usage_row(json.dumps(dict(row(1, 1.0), ts="2026-10-19T03:00:00Z")))
        self.assertEqual(zulu["time_ns"], int(datetime.datetime(2026, 10, 19, 3, tzinfo=datetime.timezone.utc).timestamp()) * 10**9)

    def test_surfaces_are_grouped_into_kinds(self):
        for surface, slot, kind in (("dashboard", "chat-1", "chat"), ("telegram", "tg:1", "chat"), ("subagent", "subagent:ab", "subagent"),
                                    ("", "subagent:ab", "subagent"), ("bg:tips", "_bg", "background"), ("", "_bg", "background"),
                                    ("cron", "cron:x", "automation"), ("heartbeat", "x", "automation"), ("workflow", "x", "automation")):
            self.assertEqual(usage.kind_of(surface, slot), kind, (surface, slot))

    def test_a_half_written_line_waits_and_a_replaced_file_is_read_again(self):
        path = self.w.usage_file()
        self.w.add_rows(row(1, 1.0), raw_tail='{"_type": "tokens", "ts"')
        lines, inode, size, start = usage.read_new_lines(path, {})
        self.assertEqual((len(lines), start), (1, 0))
        end = lines[-1][1]
        self.assertLess(end, size)
        lines, _, _, _ = usage.read_new_lines(path, {"offset": end, "inode": inode})
        self.assertEqual(lines, [])
        with open(path, "w") as handle:              # shorter than the offset: truncated or replaced
            handle.write(json.dumps(row(2, 2.0)) + "\n")
        lines, _, _, start = usage.read_new_lines(path, {"offset": end + 50, "inode": inode})
        self.assertEqual((len(lines), start), (1, 0))


class Pushing(Base):
    def test_sends_logs_and_metrics_then_only_what_is_new(self):
        self.w.add_rows(row(1, 1.25), row(2, 0.75))
        result = self.w.push()
        self.assertEqual((result.turns, round(result.credits, 3), result.errors), (2, 2.0, []))
        records = self.w.logs()
        self.assertEqual([r["attributes"]["credits"] for r in records], [1.25, 0.75])
        self.assertEqual(records[0]["resource"], {"service.name": "kiro-crew"})
        self.assertEqual(records[0]["time_ns"] // 1_000_000, ms(1))
        first = self.w.metrics()[-1]
        credits = series(first, "kiro_credits_total")
        self.assertEqual(list(credits.values()), [2.0])
        self.assertEqual(list(series(first, "kiro_turns_total").values()), [2])
        # A new series also gets a 0 point one second before, so increase() counts its first value.
        points = next(m for m in first if m["name"] == "kiro_credits_total")["points"]
        self.assertEqual([p["value"] for p in points], [0.0, 2.0])
        self.assertEqual(points[1]["time_ns"] - points[0]["time_ns"], 1_000_000_000)
        self.assertEqual(points[1]["start_ns"], self.w.state()["started_ns"])
        self.w.sent.clear()
        result = self.w.push()
        self.assertEqual(result.turns, 0)
        self.assertEqual(self.w.logs(), [])
        again = self.w.metrics()[-1]
        self.assertEqual([p["value"] for p in next(m for m in again if m["name"] == "kiro_credits_total")["points"]], [2.0])
        self.w.sent.clear()
        self.w.add_rows(row(3, 0.5))
        self.w.push()
        self.assertEqual(len(self.w.logs()), 1)
        self.assertEqual(list(series(self.w.metrics()[-1], "kiro_credits_total").values()), [2.5])

    def test_a_failed_log_send_moves_nothing_and_the_next_run_sends_it_once(self):
        self.w.add_rows(row(1, 1.0), row(2, 2.0))
        self.w.fail["logs"] = 1
        result = self.w.push()
        self.assertEqual(result.turns, 0)
        self.assertIn("HTTP 503", result.errors[0])
        state = self.w.state()
        self.assertEqual(state["totals"], {})
        self.assertIn("HTTP 503", state["last_error"])
        self.assertNotIn(os.path.join("usage", "tokens", f"{TODAY}.jsonl"), state["offsets"])
        result = self.w.push()
        self.assertEqual((result.turns, result.errors), (2, []))
        self.assertEqual(len(self.w.logs()), 2)
        self.assertEqual(list(series(self.w.metrics()[-1], "kiro_credits_total").values()), [3.0])
        self.assertEqual(self.w.state()["last_error"], "")

    def test_a_failure_in_the_second_batch_keeps_the_first(self):
        self.w.add_rows(row(1, 1.0), row(2, 2.0), row(3, 4.0))
        settings = self.w.settings(max_rows_per_request=2)
        calls = {"n": 0}

        def sender(url, payload, headers, timeout):
            if url.endswith("logs"):
                calls["n"] += 1
                if calls["n"] == 2:
                    return "HTTP 500 from x"
            self.w.sent.append(("logs" if url.endswith("logs") else "metrics", payload))
        result = usage.run_push(settings, self.w.state_dir, sender=sender)
        self.assertEqual(result.turns, 2)
        self.assertTrue(result.more_left)
        result = usage.run_push(settings, self.w.state_dir, sender=self.w.sender)
        self.assertEqual(result.turns, 1)
        self.assertEqual([r["attributes"]["credits"] for r in self.w.logs()], [1.0, 2.0, 4.0])
        self.assertEqual(list(series(self.w.metrics()[-1], "kiro_credits_total").values()), [7.0])

    def test_a_failed_metrics_send_keeps_the_counts_and_repeats_the_zero_point(self):
        self.w.add_rows(row(1, 1.0))
        self.w.fail["metrics"] = 1
        result = self.w.push()
        self.assertEqual((result.turns, len(result.errors)), (1, 1))
        self.assertEqual(self.w.state()["emitted_series"], [])
        self.w.push()
        points = next(m for m in self.w.metrics()[-1] if m["name"] == "kiro_credits_total")["points"]
        self.assertEqual([p["value"] for p in points], [0.0, 1.0])
        self.assertEqual(len(self.w.logs()), 1)        # the turn record went out once

    def test_logs_off_still_counts_metrics_and_moves_on(self):
        self.w.add_rows(row(1, 1.0))
        result = self.w.push(self.w.settings(signals=["metrics"]))
        self.assertEqual((result.turns, self.w.logs()), (1, []))
        self.assertEqual(list(series(self.w.metrics()[-1], "kiro_credits_total").values()), [1.0])
        self.assertEqual(self.w.push(self.w.settings(signals=["metrics"])).turns, 0)

    def test_old_files_are_skipped_once_and_a_half_line_waits(self):
        old = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
        self.w.add_rows(dict(row(1, 9.0), ts=ts(1, old)), day=old)
        self.w.add_rows(row(1, 1.0), raw_tail=json.dumps(row(2, 2.0))[:30])
        result = self.w.push()
        self.assertEqual((result.turns, result.skipped_old_files), (1, 1))
        with open(self.w.usage_file(), "a") as handle:
            handle.write(json.dumps(row(2, 2.0))[30:] + "\n")
        self.w.add_rows(dict(row(1, 5.0), ts=ts(2, old)), day=old)          # a late row in the skipped file is now sent
        result = self.w.push()
        self.assertEqual(sorted(r["attributes"]["credits"] for r in self.w.logs()), [1.0, 2.0, 5.0])

    def test_dry_run_sends_nothing_and_writes_nothing(self):
        self.w.add_rows(row(1, 1.0))
        result = self.w.push(self.w.settings(hash_fields=["agent"]), dry_run=True)
        self.assertEqual((result.turns, self.w.sent), (1, []))
        self.assertEqual(len(result.preview_logs), 1)
        self.assertEqual(series(result.preview_metrics, "kiro_credits_total") and 1, 1)
        self.assertFalse(os.path.exists(self.w.state_dir))
        self.assertEqual(self.w.push().turns, 1)

    def test_unreadable_rows_are_skipped_but_a_changed_format_stops(self):
        self.w.add_rows(row(1, 1.0), "garbage", row(2, 2.0))
        result = self.w.push()
        self.assertEqual((result.turns, result.unreadable), (2, 1))
        self.assertEqual(self.w.state()["unreadable_rows"], 1)
        self.w.add_rows(*[json.dumps({"_type": "tokens", "when": i}) for i in range(12)], row(3, 3.0))
        with self.assertRaises(usage.UsageError) as caught:
            self.w.push()
        self.assertIn("Crew may have changed it", str(caught.exception))
        self.assertEqual(self.w.state()["last_run"] != "", True)
        self.assertEqual(len(self.w.logs()), 2)

    def test_row_limit_and_lock(self):
        self.w.add_rows(*[row(i, 1.0) for i in range(5)])
        result = self.w.push(max_rows=2)
        self.assertEqual((result.turns, result.more_left), (2, True))
        lock = os.path.join(self.w.state_dir, "push.lock")
        with open(lock, "w") as handle:
            json.dump({"pid": os.getpid(), "host": __import__("socket").gethostname(), "since": "now"}, handle)
        with self.assertRaises(LockError):
            self.w.push()
        self.assertEqual(self.w.push(break_lock=True).turns, 3)


class Joining(Base):
    def test_subagent_spend_is_linked_to_the_session_that_spawned_it(self):
        self.w.session("s1-aaaa", "sid-1", "chat-1", workspace="ops", entries=[
            {"type": "subagent/spawned", "seq": 3, "time": ms(2), "data": {"agent_id": "kid1", "agent": "reader", "model": "qwen3-coder-next"}},
            {"type": "subagent/completed", "seq": 9, "time": ms(5), "data": {"agent_id": "kid1", "ms": 48000, "credits": 1.5}},
            {"type": "subagent/spawned", "seq": 10, "time": ms(6), "data": {"agent_id": "kid2", "agent": "writer"}},
            {"type": "subagent/failed", "seq": 11, "time": ms(7), "data": {"agent_id": "kid2", "outcome": "stopped",
                                                                           "reason": "SECRET client error text", "ms": 900}},
        ])
        self.w.add_rows(row(1, 0.3), row(5, 1.5, slot="subagent:kid1", agent="reader", surface="subagent", model="qwen3-coder-next"))
        self.w.push()
        records = self.w.logs()
        runs = [r["attributes"] for r in records if r["attributes"]["event"] == "subagent_run"]
        self.assertEqual([(r["subagent_id"], r["outcome"], r.get("subagent_credits")) for r in runs],
                         [("kid1", "completed", 1.5), ("kid2", "stopped", None)])
        self.assertEqual((runs[0]["parent_session_id"], runs[0]["root_session_key"], runs[0]["workspace"], runs[0]["model"]),
                         ("sid-1", "chat-1", "ops", "qwen3-coder-next"))
        turns = {r["attributes"]["kind"]: r["attributes"] for r in records if r["attributes"]["event"] == "turn"}
        chat, sub = turns["chat"], turns["subagent"]
        self.assertEqual((chat["session_key"], chat["session_id"], chat["root_session_id"], chat["workspace"]), ("chat-1", "sid-1", "sid-1", "ops"))
        self.assertEqual((sub["subagent_id"], sub["parent_session_key"], sub["root_session_key"], sub["workspace"]), ("kid1", "chat-1", "chat-1", "ops"))
        self.assertNotIn("session_key", sub)
        everything = json.dumps([r["attributes"] for r in records])
        self.assertNotIn("SECRET", everything)
        self.assertNotIn("/secret/client/path", everything)
        # The subagent's credits are counted once: from its usage row, not again from subagent/completed.
        self.assertEqual(sum(series(self.w.metrics()[-1], "kiro_credits_total").values()), 1.8)

    def test_a_reused_slot_picks_the_session_open_at_the_time(self):
        self.w.session("old-1111", "sid-old", "chat-1", workspace="a", created_minute=0)
        self.w.session("new-2222", "sid-new", "chat-1", workspace="b", created_minute=30)
        self.w.add_rows(row(10, 1.0), row(40, 1.0))
        self.w.push()
        self.assertEqual([r["attributes"]["session_id"] for r in self.w.logs()], ["sid-old", "sid-new"])

    def test_crew_log_events_wait_for_a_successful_send(self):
        self.w.session("s1-aaaa", "sid-1", "chat-1", entries=[
            {"type": "subagent/spawned", "time": ms(2), "data": {"agent_id": "kid1", "agent": "reader"}},
            {"type": "subagent/completed", "time": ms(5), "data": {"agent_id": "kid1", "credits": 1.0}}])
        self.w.add_rows(row(1, 0.5))
        self.w.fail["logs"] = 1
        result = self.w.push()
        self.assertEqual((result.subagent_runs, result.turns), (0, 0))
        result = self.w.push()
        self.assertEqual((result.subagent_runs, result.turns), (1, 1))
        self.assertEqual(self.w.push().subagent_runs, 0)

    def test_records_go_out_in_time_order_across_both_sources(self):
        # Loki refuses records far older than the newest it holds, so a run must never send newest-first.
        self.w.session("s1-aaaa", "sid-1", "chat-1", entries=[
            {"type": "subagent/spawned", "time": ms(1), "data": {"agent_id": "kid1", "agent": "a"}},
            {"type": "subagent/completed", "time": ms(50), "data": {"agent_id": "kid1", "credits": 1.0}}])
        self.w.session("s2-bbbb", "sid-2", "chat-2", entries=[
            {"type": "subagent/spawned", "time": ms(1), "data": {"agent_id": "kid2", "agent": "b"}},
            {"type": "subagent/completed", "time": ms(5), "data": {"agent_id": "kid2", "credits": 1.0}}])
        self.w.add_rows(row(2, 0.1), row(30, 0.1), row(55, 0.1))
        self.w.push(self.w.settings(max_rows_per_request=2))
        times = [r["time_ns"] for r in self.w.logs()]
        self.assertEqual(len(times), 5)
        self.assertEqual(times, sorted(times))

    def test_a_subagent_run_sent_before_a_failure_is_not_sent_again(self):
        self.w.session("s1-aaaa", "sid-1", "chat-1", entries=[
            {"type": "subagent/spawned", "time": ms(1), "data": {"agent_id": "kid1", "agent": "a"}},
            {"type": "subagent/completed", "time": ms(2), "data": {"agent_id": "kid1", "credits": 1.0}}])
        self.w.add_rows(row(10, 0.5), row(20, 0.5))
        settings = self.w.settings(max_rows_per_request=2)
        calls = {"n": 0}

        def sender(url, payload, headers, timeout):
            if url.endswith("logs"):
                calls["n"] += 1
                if calls["n"] == 2:
                    return "HTTP 502 from x"
            self.w.sent.append(("logs" if url.endswith("logs") else "metrics", payload))
        usage.run_push(settings, self.w.state_dir, sender=sender)     # batch 1: the run and row 10; batch 2 fails
        usage.run_push(settings, self.w.state_dir, sender=self.w.sender)
        events = [r["attributes"]["event"] for r in self.w.logs()]
        self.assertEqual(events.count("subagent_run"), 1)
        self.assertEqual(events.count("turn"), 2)
        self.assertEqual(list(series(self.w.metrics()[-1], "kiro_credits_total").values()), [1.0])

    def test_out_of_order_rows_commit_only_up_to_the_first_unsent_one(self):
        self.w.add_rows(row(30, 3.0), row(10, 1.0), row(20, 2.0))       # Crew's writes are not strictly ordered
        settings = self.w.settings(max_rows_per_request=1)
        calls = {"n": 0}

        def sender(url, payload, headers, timeout):
            if url.endswith("logs"):
                calls["n"] += 1
                if calls["n"] == 3:
                    return "HTTP 502 from x"                            # minute 10 and 20 went out, 30 did not
        result = usage.run_push(settings, self.w.state_dir, sender=sender)
        self.assertEqual(result.turns, 0)                                # row 30 is first in the file, so nothing is done yet
        self.assertEqual(self.w.state()["totals"], {})
        result = usage.run_push(settings, self.w.state_dir, sender=self.w.sender)
        self.assertEqual((result.turns, round(result.credits, 3)), (3, 6.0))

    def test_a_later_segment_is_read_too(self):
        path = self.w.session("s1-aaaa", "sid-1", "chat-1")
        with open(os.path.join(path, "log.40.jsonl"), "w") as handle:
            handle.write(json.dumps({"type": "subagent/spawned", "time": ms(2), "data": {"agent_id": "kid9", "agent": "late"}}) + "\n")
            handle.write(json.dumps({"type": "subagent/completed", "time": ms(3), "data": {"agent_id": "kid9", "credits": 0.2}}) + "\n")
        self.w.push()
        self.assertEqual([r["attributes"]["agent"] for r in self.w.logs()], ["late"])


class Privacy(Base):
    def test_only_allowlisted_fields_are_ever_sent(self):
        self.w.session("s1-aaaa", "sid-1", "chat-1", entries=[
            {"type": "subagent/spawned", "time": ms(2), "data": {"agent_id": "kid1", "agent": "reader", "task": "PROMPT TEXT"}},
            {"type": "subagent/completed", "time": ms(5), "data": {"agent_id": "kid1", "credits": 1.0, "result": "RESPONSE TEXT"}}])
        self.w.add_rows(row(1, 0.5, app="radar", prompt="PROMPT TEXT", cwd="/home/client"),
                        row(2, 0.5, slot="subagent:kid1", surface="subagent", cost=0.01, input=10, output=20))
        self.w.push()
        for record in self.w.logs():
            self.assertLessEqual(set(record["attributes"]), set(usage.SENT_FIELDS), record)
        blob = b"".join(p for _, p in self.w.sent)
        for secret in (b"PROMPT TEXT", b"RESPONSE TEXT", b"/home/client", b"/secret/client/path"):
            self.assertNotIn(secret, blob)
        names = {m["name"] for m in self.w.metrics()[-1]}
        self.assertEqual(names, {"kiro_credits_total", "kiro_turns_total", "kiro_cost_usd_total", "kiro_tokens_total",
                                 "kiro_usage_last_push_timestamp_seconds"})
        for name in names:
            self.assertRegex(name, r"^[a-z_]+$")       # Prometheus-safe as is: no renaming differences between backends
        for metric in self.w.metrics()[-1]:
            for point in metric["points"]:
                self.assertFalse({"session_key", "session_id", "subagent_id"} & set(point["attributes"]))

    def test_drop_and_hash(self):
        self.w.session("s1-aaaa", "sid-1", "chat-1", workspace="client-acme")
        self.w.add_rows(row(1, 1.0, agent="acme-auditor"))
        settings = self.w.settings(drop_fields=["workspace", "stop_reason"], hash_fields=["agent", "session_key"])
        self.w.push(settings)
        attrs = self.w.logs()[0]["attributes"]
        self.assertNotIn("workspace", attrs)
        self.assertNotIn("stop_reason", attrs)
        self.assertRegex(attrs["agent"], r"^[0-9a-f]{12}$")
        self.assertRegex(attrs["session_key"], r"^[0-9a-f]{12}$")
        self.assertEqual(attrs["root_session_key"], attrs["session_key"])      # same policy, same hash
        self.assertEqual(attrs["session_id"], "sid-1")
        labels = dict(next(iter(series(self.w.metrics()[-1], "kiro_credits_total"))))
        self.assertEqual(labels["agent"], attrs["agent"])
        self.assertNotIn("workspace", labels)
        self.assertNotIn(b"acme", b"".join(p for _, p in self.w.sent))
        salt = os.path.join(self.w.state_dir, "salt")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(salt).st_mode), 0o600)
        self.w.sent.clear()
        self.w.add_rows(row(2, 1.0, agent="acme-auditor"))
        self.w.push(settings)
        self.assertEqual(self.w.logs()[0]["attributes"]["agent"], attrs["agent"])     # stable across runs

    def test_context_can_be_dropped(self):
        self.w.add_rows(row(1, 1.0))
        self.w.push(self.w.settings(drop_fields=["context"]))
        self.assertFalse({"context_used", "context_window", "context_pct"} & set(self.w.logs()[0]["attributes"]))


class Receiver(http.server.BaseHTTPRequestHandler):
    """A tiny OTLP/HTTP receiver: stores what it gets, answers with the configured status."""

    received = []
    status = 200
    redirect = ""

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).received.append((self.path, dict(self.headers), body))
        if type(self).redirect:
            self.send_response(307)
            self.send_header("Location", type(self).redirect)
            self.end_headers()
            return
        self.send_response(type(self).status)
        self.end_headers()
        if type(self).status >= 400:
            self.wfile.write(b"rejected: Bearer s3cret-token was not accepted")

    def log_message(self, *args):
        pass


class OverHttp(Base):
    def setUp(self):
        super().setUp()
        Receiver.received, Receiver.status, Receiver.redirect = [], 200, ""
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Receiver)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.headers = os.path.join(self.w.root, "headers")
        with open(self.headers, "w") as handle:
            handle.write("Authorization: Bearer s3cret-token\n")
        os.chmod(self.headers, 0o600)

    def test_gzip_protobuf_with_the_headers_through_the_cli(self):
        self.w.add_rows(row(1, 1.0))
        self.w.settings(endpoint=self.base, headers_file=self.headers)
        code, out = self.w.cli("usage", "push")
        self.assertEqual(code, 0, out)
        self.assertIn("Sent 1 turn record(s) (1.000 credits)", out)
        paths = [p for p, _, _ in Receiver.received]
        self.assertEqual(paths, ["/v1/logs", "/v1/metrics"])
        _, headers, body = Receiver.received[0]
        self.assertEqual((headers["Content-Type"], headers["Content-Encoding"]), ("application/x-protobuf", "gzip"))
        self.assertEqual(headers["Authorization"], "Bearer s3cret-token")
        self.assertEqual(otlp.decode_logs(gzip.decompress(body))[0]["attributes"]["credits"], 1.0)
        code, out = self.w.cli("usage", "push", "--quiet")
        self.assertEqual((code, out), (0, ""))

    def test_a_rejection_is_reported_without_the_token_and_exits_1(self):
        Receiver.status = 400
        self.w.add_rows(row(1, 1.0))
        self.w.settings(endpoint=self.base, headers_file=self.headers)
        code, out = self.w.cli("usage", "push", "--quiet")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 400", out)
        self.assertNotIn("s3cret-token", out)
        self.assertIn("next run sends it again", out)
        code, out = self.w.cli("usage", "status")
        self.assertIn("Last error:", out)
        self.assertNotIn("s3cret-token", out)

    def test_redirects_are_not_followed(self):
        Receiver.redirect = self.base + "/elsewhere"
        self.w.add_rows(row(1, 1.0))
        self.w.settings(endpoint=self.base)
        code, out = self.w.cli("usage", "push")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 307", out)
        self.assertNotIn("/elsewhere", [p for p, _, _ in Receiver.received])

    def test_an_unreachable_endpoint_is_one_clear_error(self):
        self.w.add_rows(row(1, 1.0))
        self.server.shutdown()
        self.server.server_close()
        self.w.settings(endpoint=self.base, timeout_seconds=2)
        code, out = self.w.cli("usage", "push")
        self.assertEqual(code, 1)
        self.assertIn("cannot reach", out)


class Commands(Base):
    def test_init_then_off_until_an_endpoint_is_set(self):
        code, out = self.w.cli("usage", "push")
        self.assertEqual(code, 0)
        self.assertIn("off: there is no settings file", out)
        code, out = self.w.cli("usage", "init")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.w.settings_path))
        self.assertEqual(self.w.cli("usage", "init")[0], 1)
        code, out = self.w.cli("usage", "push")
        self.assertIn("off: no endpoint is set", out)
        self.assertNotIn("usage init", out)          # the file exists; the advice is to set an endpoint in it
        self.assertEqual(self.w.cli("usage", "push", "--quiet"), (0, ""))
        self.assertFalse(os.path.exists(self.w.state_dir))

    def test_dry_run_prints_the_records_and_the_series(self):
        self.w.add_rows(row(1, 1.0))
        self.w.settings()
        code, out = self.w.cli("usage", "push", "--dry-run")
        self.assertEqual(code, 0, out)
        for needle in ("Dry run: nothing was sent", "Would send 1 turn record(s)", '"credits": 1.0', "kiro_credits_total"):
            self.assertIn(needle, out)
        self.assertFalse(os.path.exists(self.w.state_dir))

    def test_status_before_and_after_a_run(self):
        self.w.settings()
        code, out = self.w.cli("usage", "status")
        self.assertIn("No run yet.", out)
        self.w.add_rows(row(1, 1.0))
        with mock.patch.object(usage, "post", self.w.sender):
            self.assertEqual(self.w.cli("usage", "push")[0], 0)
        code, out = self.w.cli("usage", "status")
        self.assertIn("Counted:    1 turn(s), 1.000 credits", out)
        self.assertIn("up to date", out)
        self.w.add_rows(row(2, 1.0))
        self.assertIn("byte(s) of usage rows not sent yet", self.w.cli("usage", "status")[1])

    def test_schedule_prints_each_kind_and_installs_nothing(self):
        launcher = os.path.join(os.path.dirname(ROOT), "bin", "crewctl")
        for kind, needle in (("cron", "*/5 * * * * mkdir -p "), ("systemd", "OnUnitActiveSec=5min"), ("launchd", "<integer>300</integer>")):
            code, out = self.w.cli("usage", "schedule", kind)
            self.assertEqual(code, 0)
            self.assertIn(needle, out)
            self.assertIn("usage", out)
            self.assertIn("push", out)
            self.assertIn("CREWCTL_PYTHON", out)                      # a scheduler's PATH has no conda or venv
            self.assertIn(usage.python_path(), out)
            self.assertIn(launcher, out)                              # not on PATH here: the repository's launcher
            self.assertIn("does not exist yet", out)
        code, out = self.w.cli("usage", "schedule", "cron", "--every", "15", "--settings", "/x/u.json")
        self.assertIn("*/15 * * * *", out)
        self.assertIn("--settings /x/u.json", out)
        # The settings file and state folder below come from CREWCTL_* variables that a scheduler will not have:
        # every line must name them anyway, or the job would read another file and resend everything.
        for kind in ("cron", "systemd", "launchd"):
            out = self.w.cli("usage", "schedule", kind)[1]
            self.assertIn(self.w.settings_path, out, kind)
            self.assertIn(f"CREWCTL_USAGE_STATE", out, kind)
            self.assertIn(self.w.state_dir, out, kind)
            self.assertNotIn("--crew-home", out, kind)
        out = self.w.cli("--crew-home", "/srv/my crew", "usage", "schedule", "systemd")[1]
        self.assertIn('"--crew-home" "/srv/my crew" "usage" "push"', out)          # quoted for systemd, before the command
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                cli.main(["usage", "schedule", "--every", "0"])

    def test_bad_settings_stop_the_push(self):
        with open(self.w.settings_path, "w") as handle:
            json.dump({"endpoint": "http://localhost:4318", "typo": 1}, handle)
        code, out = self.w.cli("usage", "push")
        self.assertEqual(code, 1)
        self.assertIn("unknown setting 'typo'", out)


if __name__ == "__main__":
    unittest.main()


class Dashboard(unittest.TestCase):
    """The shipped dashboard is the builder's output and only reads what the exporter sends."""

    def setUp(self):
        import importlib.util
        path = os.path.join(ROOT, "usage-otel", "dashboards", "build_dashboards.py")
        spec = importlib.util.spec_from_file_location("build_dashboards", path)
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        with open(os.path.join(ROOT, "usage-otel", "dashboards", "kiro-usage.json"), encoding="utf-8") as handle:
            self.shipped = json.load(handle)

    def queries(self):
        for panel in self.shipped["panels"]:
            for target in panel.get("targets", []):
                yield target["datasource"]["type"], target["expr"]

    def test_the_json_is_up_to_date_with_the_builder(self):
        built = self.builder.dashboard("kiro-crew-usage", "Kiro Crew usage", "victoriametrics-logs-datasource", self.builder.victoria_panels)
        self.assertEqual(json.loads(json.dumps(built)), self.shipped,
                         "run usage-otel/dashboards/build_dashboards.py after changing it")

    def test_every_metric_and_log_field_it_reads_is_sent(self):
        import re
        sent_metrics = {"kiro_credits_total", "kiro_turns_total", "kiro_cost_usd_total", "kiro_tokens_total",
                        "kiro_usage_last_push_timestamp_seconds"}
        kinds = set()
        for source, expr in self.queries():
            kinds.add(source)
            if source == "prometheus":
                self.assertLessEqual(set(re.findall(r"kiro_[a-z_]+", expr)), sent_metrics, expr)
                labels = set(re.findall(r'([a-z_.]+)\s*=~?\s*"', expr)) | {
                    l.strip() for group in re.findall(r"by \(([^)]*)\)", expr) for l in group.split(",")}
                self.assertLessEqual(labels, set(usage.METRIC_LABELS) | {"service.name"}, expr)
                self.assertIn('service.name=~"$service"', expr)       # every panel follows the Service filter
            else:
                fields = set(re.findall(r"\b([a-z_]+):(?:=|~|\*|\")", expr))
                fields |= {f.strip().split()[0] for group in re.findall(r"by \(([^)]*)\)", expr) for f in group.split(",")}
                fields |= set(re.findall(r"(?:sum|max)\(([a-z_]+)\)", expr))
                self.assertLessEqual(fields - {"_time", "_msg", "_stream"}, set(usage.SENT_FIELDS), expr)   # minus VictoriaLogs' own fields
                self.assertIn('{service.name=~"$service"}', expr)
        self.assertEqual(kinds, {"prometheus", "victoriametrics-logs-datasource"})

    def test_the_local_stack_wires_the_collector_to_both_stores(self):
        with open(os.path.join(ROOT, "usage-otel", "otelcol", "config.yaml"), encoding="utf-8") as handle:
            collector = handle.read()
        self.assertIn("http://victoriametrics:8428/opentelemetry/v1/metrics", collector)
        self.assertIn("http://victorialogs:9428/insert/opentelemetry/v1/logs", collector)
        with open(os.path.join(ROOT, "usage-otel", "docker-compose.yml"), encoding="utf-8") as handle:
            compose = handle.read()
        for port in ("4318", "3000", "8428", "9428"):
            self.assertIn(f'"127.0.0.1:{port}:{port}"', compose)       # never listens on other interfaces
        self.assertIn("victoriametrics-logs-datasource", compose)
        self.assertIn("image: otel/opentelemetry-collector:latest", compose)         # core: 184 MB, not contrib's 420 MB
        self.assertIn('GF_PLUGINS_PREINSTALL_DISABLED: "true"', compose)
        self.assertIn("service_completed_successfully", compose)                    # the plugin is in place before Grafana starts
