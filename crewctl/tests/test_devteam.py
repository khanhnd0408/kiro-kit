"""Tests for the devteam crew pack: its helper scripts, its agent files and its skills."""

import contextlib
import http.server
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PACK = os.path.join(ROOT, "crews", "devteam")
ASSETS = os.path.join(PACK, "skills", "assets")

from crewctl import cli  # noqa: E402


def run(script, *args, env=None, input_text=None):
    merged = {k: v for k, v in os.environ.items() if not k.startswith(("VICTORIA", "TEMPO"))}
    merged.update(env or {})
    result = subprocess.run([sys.executable, "-I", os.path.join(ASSETS, script), *args], capture_output=True, text=True,
                            env=merged, input=input_text, timeout=60)
    return result.returncode, result.stdout, result.stderr


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def file(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))
        return path


class ScoreDecision(Tmp):
    def score(self, items, **extra):
        code, out, err = run("decision/score_decision.py", "score", self.file("c.json", {"decision": "d", "items": items, **extra}))
        return code, (json.loads(out) if out else None), err

    def test_levels_follow_the_fixed_rule(self):
        yes = lambda i, w=1: {"id": i, "weight": w, "status": "yes", "evidence": "x"}
        code, result, _ = self.score([yes("a", 9), {"id": "b", "weight": 1, "status": "no"}])
        self.assertEqual((code, result["confidence"], result["coverage"]), (0, "high", 0.9))
        code, result, _ = self.score([yes("a", 6), {"id": "b", "weight": 4, "status": "no"}])
        self.assertEqual(result["confidence"], "medium")
        code, result, _ = self.score([yes("a", 1), {"id": "b", "weight": 4, "status": "unknown"}])
        self.assertEqual(result["confidence"], "low")

    def test_yes_without_evidence_counts_as_unknown(self):
        code, result, _ = self.score([{"id": "a", "weight": 1, "status": "yes", "evidence": "  "}, {"id": "b", "weight": 1, "status": "yes", "evidence": "f:1"}])
        self.assertEqual(result["unknown_share"], 0.5)
        self.assertEqual(result["open_items"], ["a"])
        self.assertTrue(any("without evidence" in n for n in result["notes"]))

    def test_unmet_blocker_blocks_even_with_high_coverage(self):
        items = [{"id": "a", "weight": 20, "status": "yes", "evidence": "x"}, {"id": "b", "weight": 1, "blocker": True, "status": "unknown"}]
        code, result, _ = self.score(items)
        self.assertEqual((result["confidence"], result["blockers_not_met"]), ("blocked", ["b"]))

    def test_na_items_do_not_count_and_bad_input_exits_2(self):
        code, result, _ = self.score([{"id": "a", "weight": 1, "status": "yes", "evidence": "x"}, {"id": "b", "weight": 50, "status": "n/a"}])
        self.assertEqual(result["confidence"], "high")
        for bad in ([], [{"id": "a", "status": "maybe"}], [{"id": "a", "status": "yes", "weight": 0, "evidence": "x"}],
                    [{"id": "a", "status": "n/a"}], [{"id": "a", "status": "no"}, {"id": "a", "status": "no"}]):
            self.assertEqual(self.score(bad)[0], 2, bad)

    def test_same_input_same_output(self):
        items = [{"id": "a", "weight": 2, "status": "yes", "evidence": "x"}, {"id": "b", "weight": 1, "status": "no"}]
        self.assertEqual(self.score(items)[1], self.score(items)[1])

    def test_wilson_interval(self):
        code, out, _ = run("decision/score_decision.py", "wilson", "8", "10")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertAlmostEqual(data["low"], 0.4902, places=3)
        self.assertAlmostEqual(data["high"], 0.9433, places=3)
        self.assertIn("anecdotal", data["note"])
        self.assertEqual(json.loads(run("decision/score_decision.py", "wilson", "50", "50")[1])["high"], 1.0)
        self.assertEqual(run("decision/score_decision.py", "wilson", "11", "10")[0], 2)

    def test_example_is_valid_input(self):
        code, out, _ = run("decision/score_decision.py", "example")
        path = self.file("e.json", out)
        self.assertEqual(run("decision/score_decision.py", "score", path)[0], 0)


class ContextGate(Tmp):
    def test_init_check_and_assumptions(self):
        code, out, _ = run("architecture/context_gate.py", "init")
        context = json.loads(out)
        self.assertEqual(code, 0)
        code, out, _ = run("architecture/context_gate.py", "check", self.file("c.json", context))
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["answered"], 0)
        for topic in context:
            context[topic] = {"answer": "something", "source": "user"}
        context["team"]["source"] = "assumed"
        code, out, _ = run("architecture/context_gate.py", "check", self.file("c.json", context))
        result = json.loads(out)
        self.assertEqual(code, 3)
        self.assertEqual([x["topic"] for x in result["needs_confirmation"]], ["team"])
        context["team"]["source"] = "user"
        code, out, _ = run("architecture/context_gate.py", "check", self.file("c.json", context))
        self.assertEqual((code, json.loads(out)["ready"]), (0, True))

    def test_bad_input(self):
        self.assertEqual(run("architecture/context_gate.py", "check", self.file("c.json", "[1]"))[0], 2)
        self.assertEqual(run("architecture/context_gate.py", "check", "/nonexistent")[0], 2)


class ChecklistStatus(Tmp):
    def status(self, text, *flags):
        code, out, _ = run("spec/checklist_status.py", self.file("c.md", text), *flags)
        return code, out

    def test_complete_only_when_every_box_ticked(self):
        self.assertEqual(self.status("- [x] a\n- [X] b\n")[0], 0)
        code, out = self.status("- [x] a\n- [ ] b\n")
        self.assertEqual(code, 3)
        self.assertIn("line 2", out)

    def test_evidence_flag(self):
        text = "- [x] R1 thing -- evidence: tests/t.py::a\n- [x] R2 other\n"
        self.assertEqual(self.status(text)[0], 0)
        code, out = self.status(text, "--require-evidence")
        self.assertEqual(code, 3)
        self.assertIn("no evidence", out)
        self.assertEqual(self.status("- [x] R1 -- evidence: ok\n", "--require-evidence")[0], 0)

    def test_empty_checklist_is_not_complete_and_template_starts_open(self):
        self.assertEqual(self.status("# nothing here\n")[0], 3)
        template = os.path.join(ASSETS, "spec", "templates", "checklist.md")
        self.assertEqual(run("spec/checklist_status.py", template, "--require-evidence")[0], 3)
        self.assertEqual(run("spec/checklist_status.py", "/nonexistent")[0], 2)
        self.assertEqual(run("spec/checklist_status.py")[0], 2)


class FakeBackends(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def _send(self, body, status=200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = self.rfile.read(length).decode()
        FakeBackends.seen.append((self.path, form, self.headers.get("Authorization")))
        if self.path == "/select/logsql/query":
            if "empty" in form:
                return self._send("")
            lines = [{"_time": f"2026-10-19T10:0{i % 6}:00Z", "_msg": f"payment timeout after {100 + i}ms id={i} token=abcdef123456", "_stream": "{app=\"pay\"}"} for i in range(5)]
            lines += [{"_time": "2026-10-19T10:09:00Z", "_msg": "db pool exhausted active=10/10", "_stream": "{app=\"db\"}"}]
            return self._send("\n".join(json.dumps(x) for x in lines))
        self._send("{}", 404)

    def do_GET(self):
        FakeBackends.seen.append((self.path, "", self.headers.get("Authorization")))
        if self.path.startswith("/api/v1/query_range"):
            if "nothing" in self.path:
                return self._send(json.dumps({"status": "success", "data": {"resultType": "matrix", "result": []}}))
            values = [[1792400000 + 60 * i, str(v)] for i, v in enumerate([1, 1, 9, 2, "NaN"])]
            return self._send(json.dumps({"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {"service": "checkout"}, "values": values}]}}))
        if self.path.startswith("/api/search"):
            return self._send(json.dumps({"traces": [{"traceID": "abc123def4567890", "rootServiceName": "gateway", "rootTraceName": "GET /pay", "durationMs": 1800}]}))
        if self.path.startswith("/api/traces/abc123def4567890"):
            span = lambda name, start, end, code: {"name": name, "startTimeUnixNano": str(start), "endTimeUnixNano": str(end), "status": {"code": code}}
            return self._send(json.dumps({"batches": [
                {"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "gateway"}}]}, "scopeSpans": [{"spans": [span("GET /pay", 0, 1_800_000_000, 0)]}]},
                {"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "payments"}}]}, "scopeSpans": [{"spans": [span("charge", 100, 1_700_000_000, "STATUS_CODE_ERROR")]}]}]}))
        self._send("{}", 404)


class CollectEvidence(Tmp):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeBackends)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def env(self):
        return {"VICTORIALOGS_URL": self.url, "VICTORIAMETRICS_URL": self.url, "TEMPO_URL": self.url, "VICTORIALOGS_TOKEN": "SECRETTOKEN12345"}

    def test_logs_group_by_signature_and_mask_secrets(self):
        FakeBackends.seen.clear()
        code, out, err = run("incident/collect_evidence.py", "logs", "--query", "error", "--start", "-10m", env=self.env())
        self.assertEqual(code, 0, err)
        self.assertIn("6 lines", out)
        self.assertRegex(out, r"\s+5\s+first")
        self.assertIn("payment timeout after Nms id=N", out)
        self.assertNotIn("abcdef123456", out)
        self.assertNotIn("SECRETTOKEN12345", out + err)
        self.assertEqual(FakeBackends.seen[0][2], "Bearer SECRETTOKEN12345")

    def test_empty_results_exit_3(self):
        self.assertEqual(run("incident/collect_evidence.py", "logs", "--query", "empty", env=self.env())[0], 3)
        self.assertEqual(run("incident/collect_evidence.py", "metrics", "--query", "nothing", env=self.env())[0], 3)

    def test_metrics_summary(self):
        code, out, err = run("incident/collect_evidence.py", "metrics", "--query", "up", "--start", "2026-10-19T09:00:00Z", "--end", "2026-10-19T10:00:00Z", env=self.env())
        self.assertEqual(code, 0, err)
        self.assertIn("service=checkout", out)
        self.assertIn("n=4", out)
        self.assertIn("max=9@", out)

    def test_traces_and_one_trace(self):
        code, out, _ = run("incident/collect_evidence.py", "traces", "--query", "{ status = error }", env=self.env())
        self.assertIn("abc123def4567890  root=gateway:GET /pay  duration=1800ms", out)
        code, out, err = run("incident/collect_evidence.py", "trace", "abc123def4567890", env=self.env())
        self.assertEqual(code, 0, err)
        self.assertIn("2 spans, 2 services", out)
        self.assertIn("ERROR payments:charge", out)

    def test_errors(self):
        self.assertEqual(run("incident/collect_evidence.py", "logs", "--query", "x")[0], 2)
        code, _, err = run("incident/collect_evidence.py", "trace", "../etc/passwd", env=self.env())
        self.assertEqual(code, 2)
        code, _, err = run("incident/collect_evidence.py", "logs", "--query", "x", "--start", "now", "--end", "-1h", env=self.env())
        self.assertEqual(code, 2)
        self.assertIn("before", err)
        code, _, err = run("incident/collect_evidence.py", "trace", "ffffffffffffffff", env=self.env())
        self.assertEqual(code, 2)
        self.assertIn("404", err)


class WatchSubagents(Tmp):
    """watch_subagents.py against a fake `kirocrew` whose `spawn list` output changes from call to call."""

    def fake(self, *frames):
        """Each call to `spawn list` prints the next frame (a string); the last frame repeats."""
        folder = os.path.join(self.tmp, "frames")
        os.makedirs(folder, exist_ok=True)
        for index, text in enumerate(frames):
            with open(os.path.join(folder, f"{index}.txt"), "w", encoding="utf-8") as handle:
                handle.write(text)
        counter = os.path.join(self.tmp, "count")
        open(counter, "w").write("0")
        script = os.path.join(self.tmp, "kirocrew")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(f"""#!{sys.executable}
import os, sys
n = int(open({counter!r}).read())
open({counter!r}, "w").write(str(n + 1))
frames = {len(frames)}
text = open(os.path.join({folder!r}, str(min(n, frames - 1)) + ".txt"), encoding="utf-8").read()
sys.stdout.write(text)
sys.exit(1 if text.startswith("Error:") else 0)
""")
        os.chmod(script, 0o755)
        return script

    def watch(self, script, *args):
        return run("orchestration/watch_subagents.py", "--kirocrew", script, *args)

    def test_returns_when_every_run_is_done_and_prints_one_line_per_round(self):
        fake = self.fake("  \u23f3 aaa111  write code\n  \u23f3 bbb222  review\n", "  \u23f3 aaa111  write code\n  \u2705 bbb222  review\n",
                         "  \u2705 aaa111  write code\n  \u2705 bbb222  review\n")
        code, out, err = self.watch(fake, "--every", "1", "--rounds", "5", "aaa111", "bbb222")
        self.assertEqual(code, 0, err)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[0], r"^round 1/5 \+0s  aaa111 running  bbb222 running$")
        self.assertRegex(lines[1], r"^round 2/5 \+1s  aaa111 running  bbb222 done$")
        self.assertRegex(lines[2], r"^round 3/5 \+2s  aaa111 done  bbb222 done$")

    def test_already_done_returns_after_one_round_without_sleeping(self):
        fake = self.fake("  \u2705 aaa111  task\n")
        started = time.monotonic()
        code, out, _ = self.watch(fake, "--every", "60", "--rounds", "5", "aaa111")
        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_rounds_used_up_exits_3_and_names_what_is_still_running(self):
        fake = self.fake("  \u23f3 aaa111  task\n  \u2705 bbb222  other\n")
        code, out, _ = self.watch(fake, "--every", "1", "--rounds", "2", "aaa111", "bbb222")
        self.assertEqual(code, 3)
        self.assertIn("rounds used up after", out)
        self.assertIn("still running: aaa111", out)
        self.assertNotIn("still running: aaa111, bbb222", out)

    def test_waiting_for_approval_exits_4(self):
        fake = self.fake("  \U0001f510 aaa111  task  \u2014 waiting for spawn approval\n")
        code, out, _ = self.watch(fake, "--every", "1", "--rounds", "3", "aaa111")
        self.assertEqual(code, 4)
        self.assertIn("Approvals", out)

    def test_unknown_id_exits_5_and_unreachable_crew_exits_2(self):
        code, _, err = self.watch(self.fake("  \u23f3 aaa111  task\n"), "--every", "1", "--rounds", "2", "zzz999")
        self.assertEqual(code, 5)
        self.assertIn("not in `kirocrew spawn list`", err)
        code, _, err = self.watch(self.fake("Error: gateway not running (cannot reach dashboard on port 8080)\n"), "aaa111")
        self.assertEqual(code, 2)
        self.assertIn("cannot reach Crew from this shell", err)
        code, _, err = self.watch(os.path.join(self.tmp, "does-not-exist"), "aaa111")
        self.assertEqual(code, 2)

    def test_probe_and_argument_checks(self):
        fake = self.fake("No subagents.\n")
        code, out, _ = self.watch(fake, "--probe")
        self.assertEqual((code, out.strip()), (0, "ok: reached Crew; 0 subagent(s) listed"))
        self.assertEqual(self.watch(fake)[0], 2)                                           # no ids
        self.assertEqual(self.watch(fake, "--every", "0", "aaa")[0], 2)
        self.assertEqual(self.watch(fake, "--rounds", "5000", "aaa")[0], 2)
        self.assertEqual(self.watch(fake, "a;rm -rf x")[0], 2)                             # not an id

    def test_parser_ignores_other_lines(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("watch_subagents", os.path.join(ASSETS, "orchestration", "watch_subagents.py"))
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "dont_write_bytecode", True):
            spec.loader.exec_module(module)                   # no __pycache__ appears in the pack
        found = module.parse_list("noise\n  \u2705 id1  a task\n  \u23f3 id2  another\ntext \u2705 not-a-row\n")
        self.assertEqual(found, {"id1": "done", "id2": "running"})


class Pack(Tmp):
    def setUp(self):
        super().setUp()
        self.agents = os.path.join(self.tmp, "agents")
        self.skills = os.path.join(self.tmp, "skills")
        self.pack = os.path.join(self.tmp, "pack")
        shutil.copytree(PACK, self.pack)
        os.makedirs(self.agents)
        with open(os.path.join(self.agents, "kirocrew.json"), "w") as handle:
            json.dump({"mcpServers": {"kirocrew-core": {"command": "/x/kirocrew", "args": ["mcp-core"]}}}, handle)

    def cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--no-color", "-c", self.pack, "--agents-dir", self.agents, "--crew-home", self.tmp, *argv])
        return code, buffer.getvalue()

    def specs(self):
        return {f[:-len(".skill.json")] for f in os.listdir(os.path.join(PACK, "skills")) if f.endswith(".skill.json")}

    def test_all_ten_crewmates_validate_on_both_plans(self):
        code, out = self.cli("validate")
        self.assertEqual(code, 0, out)
        for name in ("genai-agent", "mldl-agent", "backend-architecture-agent", "python-agent", "go-agent", "rust-agent",
                     "devsecops-agent", "inspector-agent", "spec-agent", "orchestrator-agent"):
            self.assertIn(name, out)
        with tempfile.TemporaryDirectory() as other:
            shutil.copytree(PACK, os.path.join(other, "p"))
            settings = os.path.join(other, "p", "crewctl.json")
            with open(settings, "w") as handle:
                json.dump({"plan": "paid"}, handle)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli.main(["--no-color", "-c", os.path.join(other, "p"), "--agents-dir", self.agents, "--crew-home", self.tmp, "validate"])
            self.assertEqual(code, 0, buffer.getvalue())

    def test_whole_team_builds_without_crew_servers_and_nothing_references_them(self):
        os.remove(os.path.join(self.agents, "kirocrew.json"))
        with open(os.path.join(self.pack, "crewctl.json"), "w") as handle:
            json.dump({"plan": "free", "use_crew_servers": False}, handle)
        code, out = self.cli("apply", "-y")
        self.assertEqual(code, 0, out)
        names = [f for f in os.listdir(self.agents) if f.endswith("-agent.json")]
        self.assertEqual(len(names), 10)
        for name in names:
            data = json.load(open(os.path.join(self.agents, name)))
            self.assertFalse([t for t in data["tools"] + data.get("allowedTools", []) if t.startswith("@kirocrew-")], name)
            self.assertNotIn("kirocrew-core", data.get("mcpServers", {}), name)
        self.assertIn("orchestrator-agent", out)
        self.assertIn("cannot start or watch other crewmates", out)

    def test_every_agent_skill_exists_and_agents_do_not_write_where_they_should_not(self):
        known = self.specs()
        for name in os.listdir(PACK):
            if not name.endswith(".agent.json"):
                continue
            data = json.load(open(os.path.join(PACK, name)))
            for path in data.get("skills", []):
                self.assertIn(path.split("/")[-2], known, (name, path))
            for part in data["prompt_files"]:
                self.assertTrue(os.path.exists(os.path.join(PACK, part)), (name, part))
        inspector = json.load(open(os.path.join(PACK, "inspector-agent.agent.json")))
        self.assertNotIn("web_fetch", inspector["tools"])
        for read_only in ("backend-architecture-agent",):
            self.assertNotIn("execute_bash", json.load(open(os.path.join(PACK, f"{read_only}.agent.json")))["tools"])

    def test_skills_build_lint_clean_and_every_mentioned_file_exists(self):
        with mock_env(CREWCTL_SKILLS_DIR=self.skills):
            code, out = self.cli("skills", "apply", "-y")
        self.assertEqual(code, 0, out)
        built = set(os.listdir(self.skills))
        self.assertEqual(built, self.specs())
        from crewctl.skilllint import lint_paths
        files, findings = lint_paths([self.skills])
        self.assertEqual([f.message for f in findings], [])
        for name in built:
            text = open(os.path.join(self.skills, name, "SKILL.md"), encoding="utf-8").read()
            for rel in set(re.findall(r"\{?[^\s`'\"]*?/((?:scripts|references)/[A-Za-z0-9_.-]+)", text)):
                self.assertTrue(os.path.exists(os.path.join(self.skills, name, rel)), (name, rel))
            self.assertNotIn("{{skill_dir}}", text)
            for script in os.listdir(os.path.join(self.skills, name, "scripts")) if os.path.isdir(os.path.join(self.skills, name, "scripts")) else []:
                self.assertTrue(os.access(os.path.join(self.skills, name, "scripts", script), os.X_OK), (name, script))

    def test_orchestrator_loop_uses_the_watcher_script_and_never_the_wait_tool(self):
        agent = json.load(open(os.path.join(PACK, "orchestrator-agent.agent.json")))
        prompt = self.compiled_prompts()["orchestrator-agent"]
        # no wait tool anywhere: not in the tool list, not pre-approved, not mounted from Crew
        self.assertNotIn("wait", agent["crew_servers"]["core"]["tools"])
        self.assertNotIn("@kirocrew-core/wait", agent["allowed_tools"])
        self.assertIn("You never use the wait tool", prompt)
        self.assertNotIn("Call wait with", prompt)
        for tool in ("spawn_run", "spawn_status", "spawn_list", "spawn_steer", "spawn_continue", "ask_question"):
            self.assertIn(f"@kirocrew-core/{tool}", agent["allowed_tools"])
            self.assertIn(tool, agent["crew_servers"]["core"]["tools"])
        # spawn_status is used each call, so it must be pre-approved for an unattended loop
        self.assertIn("@kirocrew-core/spawn_status", agent["allowed_tools"])
        # the settings the user can override
        every = int(re.search(r"CHECK_EVERY_SECONDS = (\d+)", prompt).group(1))
        rounds = int(re.search(r"MAX_ROUNDS = (\d+)", prompt).group(1))
        per_call = int(re.search(r"ROUNDS_PER_CALL = (\d+)", prompt).group(1))
        self.assertTrue(1 <= every <= 3600 and per_call <= rounds, (every, rounds, per_call))
        self.assertTrue(every * rounds <= 14400, (every, rounds))
        # all other crewmates are named, with the crew field
        for name in ("spec-agent", "backend-architecture-agent", "python-agent", "go-agent", "rust-agent", "genai-agent",
                     "mldl-agent", "devsecops-agent", "inspector-agent"):
            self.assertIn(name, prompt)
        self.assertIn("`crew` field", prompt)

    def test_orchestrator_shell_permission_allows_only_the_watcher_command(self):
        agent = json.load(open(os.path.join(PACK, "orchestrator-agent.agent.json")))
        self.assertIn("execute_bash", agent["tools"])
        self.assertNotIn("execute_bash", agent["allowed_tools"])               # otherwise every command would be pre-approved
        allowed = agent["extra_agent_fields"]["toolsSettings"]["execute_bash"]["allowedCommands"]
        self.assertEqual(len(allowed), 1)
        pattern = re.compile(allowed[0])
        good = "python3 ~/.kiro/crew/skills/subagent-watch/scripts/watch_subagents.py --every 30 --rounds 10 a1b2c3 d4e5f6"
        self.assertTrue(pattern.match(good))
        self.assertTrue(pattern.match("python3 ~/.kiro/crew/skills/subagent-watch/scripts/watch_subagents.py --probe"))
        for bad in (good + "; rm -rf ~", good + " && curl x", good + " | sh", good + " $(id)", good + " `id`", good + " > /tmp/x",
                    "python3 ~/.kiro/crew/skills/other/scripts/watch_subagents.py --probe",
                    "python3 /tmp/watch_subagents.py --probe", "rm -rf ~", "sleep 30"):
            self.assertFalse(pattern.match(bad), bad)
        prompt = self.compiled_prompts()["orchestrator-agent"]
        self.assertIn("python3 ~/.kiro/crew/skills/subagent-watch/scripts/watch_subagents.py", prompt)
        self.assertEqual(agent["skills"], ["~/.kiro/crew/skills/subagent-watch/SKILL.md"])

    def compiled_prompts(self):
        from crewctl.config import load_config
        _, specs, problems = load_config(self.pack, {"agents_dir": self.agents, "crew_home": self.tmp})
        self.assertEqual([p.message for p in problems if p.level == "error"], [])
        return {spec.name: spec.prompt for spec in specs}

    def test_every_prompt_carries_the_language_rules(self):
        language = open(os.path.join(PACK, "prompts", "_language.md"), encoding="utf-8").read().strip()
        prompts = self.compiled_prompts()
        self.assertEqual(len(prompts), 10)
        for name, prompt in prompts.items():
            self.assertIn(language, prompt, name)
            self.assertTrue(prompt.rstrip().endswith(language.splitlines()[-1]), f"{name}: language rules must come last")
            self.assertNotIn("user's language", prompt, name)
        for needle in ("Think, plan and reason in English only", "Vietnamese is for the person only", 'begins with "SUBAGENT TASK"',
                       "never use ask_question"):
            self.assertIn(needle, language)

    def test_orchestrator_briefs_specialists_in_english_and_reports_in_vietnamese(self):
        prompt = self.compiled_prompts()["orchestrator-agent"]
        self.assertIn("SUBAGENT TASK from orchestrator-agent. Reply in English only", prompt)
        self.assertIn("Final report to the user, in Vietnamese", prompt)
        self.assertIn("ask_question tool (header, question and option text in Vietnamese)", prompt)
        self.assertIn("progress lines in the loop below stay English", prompt)

    def test_agents_that_ask_the_user_can_use_the_question_card(self):
        for name in ("orchestrator-agent", "backend-architecture-agent", "spec-agent"):
            agent = json.load(open(os.path.join(PACK, f"{name}.agent.json")))
            self.assertIn("ask_question", agent["crew_servers"]["core"]["tools"], name)
            self.assertIn("@kirocrew-core/ask_question", agent["allowed_tools"], name)
        prompts = self.compiled_prompts()
        self.assertIn("ask_question tool", prompts["backend-architecture-agent"])
        self.assertIn("ask_question tool", prompts["spec-agent"])
        self.assertIn("English, even when the user writes Vietnamese", prompts["spec-agent"])

    def test_no_credentials_or_personal_text_in_the_pack(self):
        from crewctl.config import SECRET_PATTERNS
        for folder, dirs, files in os.walk(PACK):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                text = open(os.path.join(folder, f), encoding="utf-8").read()
                for pattern in SECRET_PATTERNS:
                    self.assertIsNone(pattern.search(text), (folder, f))
                self.assertIsNone(re.search(r"/home/\w+|/Users/\w+|[\w.+-]+@[\w-]+\.[a-z]{2,}", text), (folder, f))


@contextlib.contextmanager
def mock_env(**values):
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
