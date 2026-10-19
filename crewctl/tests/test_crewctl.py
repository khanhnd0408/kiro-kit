"""Tests for crewctl. Every test works in temp folders with HOME pointed at a temp folder, so
nothing here can touch a real ~/.kiro. The only program ever executed is a fake `kirocrew` script
that the tests write themselves.

Run:  python3 -m unittest discover -s tests -v     (from the crewctl folder)
"""

import contextlib
import io
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from crewctl import cli, config, engine, models, util  # noqa: E402
from crewctl.state import load_state, lock_path  # noqa: E402


class Env:
    """A throwaway world: config folder, agents folder, fake Crew home."""

    def __init__(self, crew_agents=None, workspaces=None, provider_models=None, with_crew_config=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.home = os.path.join(self.root, "home")
        self.cfg = os.path.join(self.root, "crew")
        self.agents = os.path.join(self.root, "agents")
        self.crew_home = os.path.join(self.root, "crewhome")
        for folder in (self.home, self.cfg, self.agents, self.crew_home, os.path.join(self.cfg, "prompts")):
            os.makedirs(folder)
        if with_crew_config:
            data = {"agents": crew_agents if crew_agents is not None else {
                        "default": {"kiro_agent": "kirocrew", "workspace": "default", "memory_store": "default"}},
                    "workspaces": workspaces if workspaces is not None else {"default": {"dir": "workspace"}},
                    "telegram": {"bot_token": "TOPSECRET-TOKEN-VALUE-12345"}}
            self.write_json(os.path.join(self.crew_home, "config.json"), data)
        if provider_models is not None:
            self.write_json(os.path.join(self.crew_home, "provider_models.json"), {"acp": provider_models})
        self.patch = mock.patch.dict(os.environ, {"HOME": self.home}, clear=False)
        self.patch.start()
        for key in ("CREWCTL_AGENTS_DIR", "CREWCTL_CREW_HOME", "CREWCTL_DIR", "KIROCREW_HOME", "NO_COLOR"):
            os.environ.pop(key, None)

    def close(self):
        self.patch.stop()
        self.tmp.cleanup()

    @staticmethod
    def write_json(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def agent(self, name, **fields):
        data = {"name": name, "description": "d", "triggers": "t", "model": "qwen3-coder-next",
                "prompt": "You do things.", "tools": ["fs_read"]}
        data.update(fields)
        data = {k: v for k, v in data.items() if v is not None}
        self.write_json(os.path.join(self.cfg, f"{name.replace(' ', '-')}.agent.json"), data)

    def settings_file(self, **fields):
        self.write_json(os.path.join(self.cfg, "crewctl.json"), fields)

    def run(self, *argv):
        buffer = io.StringIO()
        base = ["-c", self.cfg, "--agents-dir", self.agents, "--crew-home", self.crew_home, "--no-color"]
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = cli.main(base + list(argv))
        return code, buffer.getvalue()

    def ctx(self):
        return engine.build_context(self.cfg, {"agents_dir": self.agents, "crew_home": self.crew_home})

    def agent_file(self, kiro_agent):
        return os.path.join(self.agents, f"{kiro_agent}.json")

    def read_agent_file(self, kiro_agent):
        with open(self.agent_file(kiro_agent), encoding="utf-8") as handle:
            return json.load(handle)


class Base(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)


def errors_of(env):
    ctx = env.ctx()
    return [p.message for p in ctx.problems if p.level == "error"]


class SettingsPrecedence(Base):
    def test_environment_variable_is_used_when_no_flag(self):
        env = self.env
        other = os.path.join(env.root, "other-agents")
        env.agent("a")
        with mock.patch.dict(os.environ, {"CREWCTL_AGENTS_DIR": other}):
            ctx = engine.build_context(env.cfg, {"agents_dir": None, "crew_home": env.crew_home})
        self.assertEqual(ctx.settings.agents_dir, other)

    def test_flag_beats_environment(self):
        env = self.env
        with mock.patch.dict(os.environ, {"CREWCTL_AGENTS_DIR": "/nonexistent/env"}):
            ctx = engine.build_context(env.cfg, {"agents_dir": env.agents})
        self.assertEqual(ctx.settings.agents_dir, env.agents)

    def test_default_agents_dir_is_under_home(self):
        ctx = engine.build_context(self.env.cfg, {})
        self.assertEqual(ctx.settings.agents_dir, os.path.join(self.env.home, ".kiro", "agents"))

    def test_unknown_setting_is_an_error(self):
        self.env.settings_file(plan="free", colour="blue")
        self.assertTrue(any("unknown setting 'colour'" in e for e in errors_of(self.env)))

    def test_bad_plan_value(self):
        self.env.settings_file(plan="premium")
        self.assertTrue(any("plan must be" in e for e in errors_of(self.env)))


class Validation(Base):
    def assert_error(self, text):
        found = errors_of(self.env)
        self.assertTrue(any(text in e for e in found), f"expected an error containing {text!r}, got {found}")

    def test_valid_agent(self):
        self.env.agent("log-analyst")
        self.assertEqual(errors_of(self.env), [])

    def test_name_default_is_rejected(self):
        self.env.agent("default")
        self.assert_error("'default'")

    def test_name_with_space_needs_a_file_name(self):
        self.env.agent("Log Analyst")
        self.assert_error("not valid")
        self.env.agent("Log Analyst", kiro_agent="log-analyst")
        self.assertEqual(errors_of(self.env), [])

    def test_reserved_and_kirocrew_names(self):
        self.env.agent("x1", kiro_agent="plan")
        self.env.agent("x2", kiro_agent="kirocrew-custom")
        found = errors_of(self.env)
        self.assertTrue(any("reserved by Kiro" in e for e in found))
        self.assertTrue(any("belong to Crew" in e for e in found))

    def test_unknown_field_is_an_error(self):
        self.env.agent("a", modle="x")
        self.assert_error("unknown field 'modle'")

    def test_tools_are_required_and_have_no_default(self):
        self.env.agent("a", tools=None)
        self.assert_error("\"tools\" is required")

    def test_star_tools_warns(self):
        self.env.agent("a", tools=["*"])
        self.assertTrue(any("every tool" in p.message for p in self.env.ctx().problems if p.level == "warn"))

    def test_prompt_and_prompt_file_are_exclusive(self):
        self.env.agent("a", prompt_file="prompts/p.md")
        self.assert_error("exactly one")

    def test_prompt_file_is_read_and_must_stay_inside(self):
        with open(os.path.join(self.env.cfg, "prompts", "p.md"), "w", encoding="utf-8") as handle:
            handle.write("From a file.")
        self.env.agent("a", prompt=None, prompt_file="prompts/p.md")
        ctx = self.env.ctx()
        self.assertEqual(ctx.specs[0].prompt, "From a file.")
        self.env.agent("b", prompt=None, prompt_file="../outside.md")
        self.assertTrue(any("inside the config folder" in e for e in errors_of(self.env)))

    def test_prompt_file_symlink_escape_is_rejected(self):
        secret = os.path.join(self.env.root, "secret.txt")
        with open(secret, "w", encoding="utf-8") as handle:
            handle.write("outside")
        os.symlink(secret, os.path.join(self.env.cfg, "prompts", "link.md"))
        self.env.agent("a", prompt=None, prompt_file="prompts/link.md")
        self.assert_error("inside the config folder")

    def test_credentials_are_rejected_everywhere(self):
        fake_key = "ghp_" + "a1B2c3D4e5" * 4
        self.env.agent("a", mcp_servers={"x": {"command": "tool", "env": {"TOKEN": fake_key}}})
        self.env.agent("b", prompt=f"use {fake_key} to log in")
        found = errors_of(self.env)
        self.assertEqual(sum("looks like a real credential" in e for e in found), 2)

    def test_mcp_needs_command_or_url(self):
        self.env.agent("a", mcp_servers={"x": {}})
        self.assert_error("exactly one of")

    def test_permissions_shape(self):
        self.env.agent("a", permissions={"rules": [{"capability": "teleport", "effect": "maybe"}]})
        found = errors_of(self.env)
        self.assertTrue(any("capability must be one of" in e for e in found))
        self.assertTrue(any("effect must be allow, ask or deny" in e for e in found))

    def test_effort_values(self):
        self.env.agent("a", reasoning_effort="ludicrous")
        self.assert_error("reasoning_effort must be one of")
        self.env.agent("a", reasoning_effort="high")
        self.assertEqual(errors_of(self.env), [])

    def test_duplicate_names_and_files(self):
        self.env.agent("a")
        self.env.write_json(os.path.join(self.env.cfg, "sub", "copy.agent.json"),
                            {"name": "a", "prompt": "x", "tools": []})
        self.assert_error("also used in")
        os.remove(os.path.join(self.env.cfg, "sub", "copy.agent.json"))
        self.env.write_json(os.path.join(self.env.cfg, "sub", "copy.agent.json"),
                            {"name": "b", "kiro_agent": "A", "prompt": "x", "tools": []})
        self.assert_error("agent file name 'A' is also used")

    def test_workspace_dir_cannot_escape(self):
        self.env.agent("a", workspace="w", workspace_dir="../evil")
        self.assert_error("workspace_dir")

    def test_session_color(self):
        self.env.agent("a", session_color="red")
        self.assert_error("session_color")

    def test_extra_fields_cannot_override_managed_keys(self):
        self.env.agent("a", extra_agent_fields={"model": "x"})
        self.assert_error("may not set model")

    def test_defaults_fill_gaps_but_do_not_override(self):
        self.env.write_json(os.path.join(self.env.cfg, "_defaults.json"), {"model": "glm-5", "tools": ["grep"]})
        self.env.agent("a", model=None, tools=None)
        self.env.agent("b", model="deepseek-3.2", tools=["fs_read"])
        specs = {s.name: s for s in self.env.ctx().specs}
        self.assertEqual((specs["a"].model, specs["a"].tools), ("glm-5", ["grep"]))
        self.assertEqual((specs["b"].model, specs["b"].tools), ("deepseek-3.2", ["fs_read"]))

    def test_invalid_json_reports_the_file(self):
        with open(os.path.join(self.env.cfg, "broken.agent.json"), "w", encoding="utf-8") as handle:
            handle.write("{ nope")
        self.assert_error("not valid JSON")

    def test_underscore_files_are_ignored(self):
        self.env.write_json(os.path.join(self.env.cfg, "_template.agent.json"), {"name": "x"})
        self.assertEqual(self.env.ctx().specs, [])


class ModelChecks(unittest.TestCase):
    def test_free_plan_rejects_paid_model(self):
        out = models.check_model("claude-opus-5.5", "free", None)
        self.assertTrue(any(level == "error" and "paid" in msg for level, msg in out))

    def test_free_plan_accepts_free_model_and_auto(self):
        self.assertEqual(models.check_model("qwen3-coder-next", "free", None), [])
        self.assertEqual(models.check_model("auto", "free", None), [])
        self.assertEqual(models.check_model("", "free", None), [])

    def test_paid_plan_allows_opus_with_a_naming_note(self):
        out = models.check_model("claude-opus-5.5", "paid", None)
        self.assertFalse(any(level == "error" for level, _ in out))
        self.assertTrue(any("inferred" in msg for _, msg in out))

    def test_paid_plan_warns_when_not_in_local_list(self):
        out = models.check_model("claude-opus-5.5", "paid", {"auto", "glm-5"})
        self.assertTrue(any(level == "warn" and "local Crew model list" in msg for level, msg in out))

    def test_model_in_local_list_is_ok_on_free_plan(self):
        self.assertEqual([m for m in models.check_model("claude-haiku-4.5", "free", {"claude-haiku-4.5"}) if m[0] != "info"], [])

    def test_undocumented_free_model_only_warns(self):
        out = models.check_model("claude-haiku-4.5", "free", None)
        self.assertEqual([level for level, _ in out], ["warn"])

    def test_unknown_model_is_an_error_unless_allowed(self):
        self.assertEqual(models.check_model("gpt-9", "free", None)[0][0], "error")
        self.assertEqual(models.check_model("gpt-9", "free", None, allow_unknown=True)[0][0], "warn")

    def test_end_of_life_model(self):
        out = models.check_model("claude-sonnet-4", "paid", None)
        self.assertTrue(any(level == "error" and "end of life" in msg for level, msg in out))

    def test_catalog_integrity(self):
        seen = set()
        for entry in models.catalog().values():
            self.assertNotIn(entry["id"], seen)
            seen.add(entry["id"])
            for key in ("id", "name", "credits", "credits_over_272k", "context_tokens", "free_tier", "status", "id_status", "source"):
                self.assertIn(key, entry)
            self.assertIn(entry["status"], ("active", "experimental", "end-of-life"))
            self.assertIn(entry["id_status"], ("seen-in-crew-cache", "seen-in-crew-source", "by-pattern"))

    def test_user_supplied_prices(self):
        c = models.catalog()
        expected = {"claude-opus-5.5": 2.0, "claude-sonnet-5.5": 1.3, "gpt-5.6-sol": 4.4, "gpt-5.6-terra": 2.2,
                    "gpt-5.6-luna": 0.6, "claude-haiku-4.5": 0.4, "deepseek-3.2": 0.25, "minimax-m2.5": 0.25,
                    "glm-5": 0.5, "qwen3-coder-next": 0.05}
        for model_id, credits in expected.items():
            self.assertEqual(c[model_id]["credits"], credits, model_id)
        for model_id in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
            self.assertAlmostEqual(c[model_id]["credits_over_272k"], 2 * c[model_id]["credits"])

    def test_model_notes_mention_every_catalog_model(self):
        notes = os.path.join(ROOT, "docs", "model-notes.md")
        with open(notes, encoding="utf-8") as handle:
            text = handle.read()
        for model_id in models.catalog():
            self.assertIn(model_id, text, f"{model_id} is missing from docs/model-notes.md")


class Mapping(Base):
    def test_agent_file_content_and_key_order(self):
        self.env.agent("a", allowed_tools=["fs_read"], resources=["file://.kiro/steering/**/*.md"],
                       extra_agent_fields={"welcomeMessage": "hi"})
        self.assertEqual(self.env.run("apply", "--yes")[0], 0)
        data = self.env.read_agent_file("a")
        self.assertEqual(list(data), ["name", "description", "model", "prompt", "tools", "allowedTools", "mcpServers",
                                      "includeMcpJson", "resources", "welcomeMessage"])
        self.assertEqual(data["model"], "qwen3-coder-next")
        self.assertEqual(data["mcpServers"], {})
        self.assertFalse(data["includeMcpJson"])

    def test_unset_model_becomes_auto(self):
        self.env.agent("a", model="")
        self.env.run("apply", "--yes")
        self.assertEqual(self.env.read_agent_file("a")["model"], "auto")

    def test_optional_keys_are_left_out(self):
        self.env.agent("a", description="")
        self.env.run("apply", "--yes")
        data = self.env.read_agent_file("a")
        for key in ("description", "allowedTools", "resources", "permissions", "hooks"):
            self.assertNotIn(key, data)

    def test_agent_file_is_private(self):
        self.env.agent("a")
        self.env.run("apply", "--yes")
        mode = stat.S_IMODE(os.stat(self.env.agent_file("a")).st_mode)
        self.assertEqual(mode, 0o600)


class Lifecycle(Base):
    def test_plan_does_not_write_anything(self):
        self.env.agent("a")
        code, text = self.env.run("plan")
        self.assertEqual(code, 0)
        self.assertIn("1 to create", text)
        self.assertEqual(os.listdir(self.env.agents), [])
        self.assertFalse(os.path.exists(os.path.join(self.env.cfg, ".crewctl")))

    def test_create_then_noop_then_update_then_delete(self):
        env = self.env
        env.agent("a")
        code, text = env.run("apply", "--yes")
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.isfile(env.agent_file("a")))
        state = load_state(env.cfg)
        self.assertEqual(state.agents["a"]["file_sha256"], util.sha256_file(env.agent_file("a")))
        self.assertEqual(state.serial, 1)

        code, text = env.run("plan", "--detailed-exitcode")
        self.assertEqual(code, 0)
        self.assertIn("No changes", text)

        env.agent("a", model="glm-5")
        code, text = env.run("plan", "--detailed-exitcode")
        self.assertEqual(code, 2)
        self.assertIn('-  "model": "qwen3-coder-next"', text.replace("    ", "", 0))
        self.assertEqual(env.run("apply", "--yes")[0], 0)
        self.assertEqual(env.read_agent_file("a")["model"], "glm-5")

        os.remove(os.path.join(env.cfg, "a.agent.json"))
        code, text = env.run("apply", "--yes")
        self.assertEqual(code, 0, text)
        self.assertFalse(os.path.exists(env.agent_file("a")))
        self.assertEqual(load_state(env.cfg).agents, {})

    def test_apply_without_yes_in_a_non_terminal_changes_nothing(self):
        self.env.agent("a")
        code, text = self.env.run("apply")
        self.assertEqual(code, 1)
        self.assertIn("nothing was changed", text)
        self.assertEqual(os.listdir(self.env.agents), [])

    def test_drift_blocks_apply_until_forced_and_keeps_a_backup(self):
        env = self.env
        env.agent("a")
        env.run("apply", "--yes")
        with open(env.agent_file("a"), "a", encoding="utf-8") as handle:
            handle.write(" ")                        # someone edited the file
        edited = util.sha256_file(env.agent_file("a"))
        code, text = env.run("plan")
        self.assertIn("DRIFT", text)
        code, text = env.run("apply", "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(util.sha256_file(env.agent_file("a")), edited)      # untouched
        code, text = env.run("apply", "--yes", "--force")
        self.assertEqual(code, 0, text)
        self.assertNotEqual(util.sha256_file(env.agent_file("a")), edited)
        backups = os.path.join(env.cfg, ".crewctl", "backups")
        copies = [os.path.join(d, f) for d, _, files in os.walk(backups) for f in files]
        self.assertEqual(len(copies), 1)
        self.assertEqual(util.sha256_file(copies[0]), edited)                 # the edit is recoverable

    def test_file_deleted_outside_is_recreated(self):
        env = self.env
        env.agent("a")
        env.run("apply", "--yes")
        os.remove(env.agent_file("a"))
        code, text = env.run("plan")
        self.assertIn("missing on disk", text)
        self.assertEqual(env.run("apply", "--yes")[0], 0)
        self.assertTrue(os.path.isfile(env.agent_file("a")))

    def test_unmanaged_existing_file_is_a_conflict_and_is_never_overwritten(self):
        env = self.env
        env.agent("a")
        with open(env.agent_file("a"), "w", encoding="utf-8") as handle:
            handle.write("{\"name\": \"hand-made\"}")
        code, text = env.run("apply", "--yes", "--force")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT", text)
        with open(env.agent_file("a"), encoding="utf-8") as handle:
            self.assertIn("hand-made", handle.read())

    def test_import_adopts_then_plan_shows_the_update(self):
        env = self.env
        env.agent("a")
        with open(env.agent_file("a"), "w", encoding="utf-8") as handle:
            handle.write("{\"name\": \"a\"}")
        self.assertEqual(env.run("import", "a")[0], 0)
        code, text = env.run("plan")
        self.assertIn("(update: a.json)", text)
        self.assertNotIn("DRIFT", text)     # the imported hash is the baseline, so this is a normal update
        self.assertEqual(env.run("apply", "--yes")[0], 0)
        self.assertEqual(env.read_agent_file("a")["prompt"], "You do things.")
        self.assertEqual(env.run("plan", "--detailed-exitcode")[0], 0)

    def test_import_refuses_unknown_name_and_missing_file(self):
        env = self.env
        self.assertEqual(env.run("import", "ghost")[0], 1)
        env.agent("a")
        code, text = env.run("import", "a")
        self.assertEqual(code, 1)
        self.assertIn("does not exist", text)

    def test_changing_kiro_agent_replaces_the_file(self):
        env = self.env
        env.agent("a")
        env.run("apply", "--yes")
        env.agent("a", kiro_agent="a-v2")
        code, text = env.run("plan")
        self.assertIn("replace", text)
        self.assertEqual(env.run("apply", "--yes")[0], 0)
        self.assertFalse(os.path.exists(env.agent_file("a")))
        self.assertTrue(os.path.exists(env.agent_file("a-v2")))
        self.assertEqual(load_state(env.cfg).agents["a"]["kiro_agent"], "a-v2")

    def test_rename_needs_state_mv_and_then_works(self):
        env = self.env
        env.agent("old", kiro_agent="shared")
        env.run("apply", "--yes")
        os.remove(os.path.join(env.cfg, "old.agent.json"))
        env.agent("new", kiro_agent="shared")
        code, text = env.run("plan")
        self.assertIn("state mv old new", text)
        self.assertIn("CONFLICT", text)
        before = util.sha256_file(env.agent_file("shared"))
        self.assertEqual(env.run("apply", "--yes")[0], 1)                  # blocked: nothing deleted
        self.assertEqual(util.sha256_file(env.agent_file("shared")), before)
        self.assertEqual(env.run("state", "mv", "old", "new")[0], 0)
        code, text = env.run("plan", "--detailed-exitcode")
        self.assertEqual(code, 0, text)

    def test_target_limits_the_plan(self):
        env = self.env
        env.agent("a")
        env.agent("b")
        code, text = env.run("apply", "--yes", "--target", "a")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(env.agent_file("a")))
        self.assertFalse(os.path.exists(env.agent_file("b")))

    def test_destroy_removes_only_managed_files(self):
        env = self.env
        env.agent("a")
        env.run("apply", "--yes")
        with open(env.agent_file("hand-made"), "w", encoding="utf-8") as handle:
            handle.write("{}")
        code, text = env.run("destroy", "--yes")
        self.assertEqual(code, 0, text)
        self.assertFalse(os.path.exists(env.agent_file("a")))
        self.assertTrue(os.path.exists(env.agent_file("hand-made")))
        self.assertTrue(os.path.exists(os.path.join(env.cfg, "a.agent.json")))     # config is never deleted

    def test_destroy_with_nothing_managed(self):
        code, text = self.env.run("destroy", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to destroy", text)

    def test_invalid_config_blocks_apply(self):
        self.env.agent("a", tools=None)
        code, text = self.env.run("apply", "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(os.listdir(self.env.agents), [])

    def test_free_plan_blocks_paid_model_but_paid_plan_allows(self):
        env = self.env
        env.agent("a", model="claude-opus-5.5")
        env.settings_file(plan="free")
        self.assertEqual(env.run("apply", "--yes")[0], 1)
        env.settings_file(plan="paid")
        self.assertEqual(env.run("apply", "--yes")[0], 0)

    def test_default_plan_is_free_when_nothing_says_otherwise(self):
        self.assertEqual(self.env.ctx().settings.plan, "free")

    def test_paid_model_is_ignored_on_the_free_plan_and_used_on_paid(self):
        env = self.env
        env.agent("a", model="deepseek-3.2", paid_model="claude-opus-5.5")
        env.settings_file(plan="free")
        self.assertEqual(env.run("apply", "--yes")[0], 0)                       # the paid choice does not block Free
        self.assertEqual(env.read_agent_file("a")["model"], "deepseek-3.2")
        env.settings_file(plan="paid")                                           # moving to the licensed machine
        code, text = env.run("plan")
        self.assertIn('"model": "claude-opus-5.5"', text)
        self.assertEqual(env.run("apply", "--yes")[0], 0)
        self.assertEqual(env.read_agent_file("a")["model"], "claude-opus-5.5")
        env.settings_file(plan="free")                                           # and back
        self.assertEqual(env.run("apply", "--yes")[0], 0)
        self.assertEqual(env.read_agent_file("a")["model"], "deepseek-3.2")

    def test_paid_plan_without_paid_model_uses_model(self):
        env = self.env
        env.agent("a", model="glm-5")
        env.settings_file(plan="paid")
        env.run("apply", "--yes")
        self.assertEqual(env.read_agent_file("a")["model"], "glm-5")

    def test_a_typo_in_the_unused_model_is_still_caught(self):
        env = self.env
        env.agent("a", model="deepseek-3.2", paid_model="claude-opus-9")
        env.settings_file(plan="free")
        self.assertTrue(any("paid_model" in e and "unknown model" in e for e in errors_of(env)))
        env.agent("b", model="claude-sonnet-4", paid_model="")                   # retired model, on the paid side
        env.settings_file(plan="paid")
        env.agent("a", model="deepseek-3.2", paid_model="claude-opus-5.5")
        self.assertTrue(any("end of life" in e for e in errors_of(env)))

    def test_shipped_examples_work_on_both_plans(self):
        env = self.env
        for plan, expected in (("free", {"conductor": "claude-sonnet-4.5", "log-analyst": "qwen3-coder-next",
                                         "change-reviewer": "deepseek-3.2"}),
                               ("paid", {"conductor": "claude-opus-5.5", "log-analyst": "deepseek-3.2",
                                         "change-reviewer": "claude-sonnet-5.5"})):
            work = os.path.join(env.root, f"ex-{plan}")
            shutil.copytree(os.path.join(ROOT, "examples", "crew"), work)
            env.write_json(os.path.join(work, "crewctl.json"), {"plan": plan})
            ctx = engine.build_context(work, {"agents_dir": env.agents, "crew_home": env.crew_home})
            self.assertEqual([str(p) for p in ctx.problems if p.level == "error"], [], plan)
            self.assertEqual({s.name: s.model for s in ctx.specs}, expected)

    def test_paid_plan_warns_but_does_not_block_a_model_missing_from_the_local_list(self):
        env = Env(provider_models=["auto", "glm-5"])          # a Crew list without Opus
        self.addCleanup(env.close)
        env.settings_file(plan="paid")
        env.agent("a", model="claude-opus-5.5")
        code, text = env.run("plan")
        self.assertEqual(code, 0)
        self.assertIn("not in your local Crew model list", text)
        self.assertEqual(env.run("apply", "--yes")[0], 0)

    def test_paid_machine_with_the_model_in_its_list_has_no_warning(self):
        env = Env(provider_models=["auto", "claude-opus-5.5", "gpt-5.6-sol"])
        self.addCleanup(env.close)
        env.settings_file(plan="paid")
        env.agent("a", model="claude-opus-5.5")
        env.agent("b", model="gpt-5.6-sol")
        warnings = [p for p in env.ctx().problems if p.level == "warn" and "model" in p.message]
        self.assertEqual(warnings, [])

    def test_corrupt_state_gives_a_clear_error(self):
        env = self.env
        env.agent("a")
        env.run("apply", "--yes")
        with open(os.path.join(env.cfg, ".crewctl", "state.json"), "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        code, text = env.run("plan")
        self.assertEqual(code, 1)
        self.assertIn("cannot read", text)

    def test_state_commands(self):
        env = self.env
        env.agent("a")
        env.run("apply", "--yes")
        self.assertIn("a.json", env.run("state", "list")[1])
        self.assertIn("file_sha256", env.run("state", "show", "a")[1])
        self.assertEqual(env.run("state", "rm", "a")[0], 0)
        self.assertTrue(os.path.exists(env.agent_file("a")))        # forgetting does not delete
        self.assertEqual(env.run("state", "rm", "a")[0], 1)


class Locking(Base):
    def test_windows_never_probes_a_process(self):
        with mock.patch.object(os, "name", "nt"), mock.patch.object(os, "kill") as kill:
            self.assertTrue(util.FileLock._alive(12345))
        kill.assert_not_called()

    def test_live_lock_blocks_and_stale_lock_is_taken_over(self):
        env = self.env
        env.agent("a")
        path = lock_path(env.cfg)
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "since": "now"}, handle)
        code, text = env.run("apply", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("locked", text)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"pid": 2 ** 22 + 12345, "host": socket.gethostname(), "since": "then"}, handle)
        self.assertEqual(env.run("apply", "--yes")[0], 0)
        self.assertFalse(os.path.exists(path))

    def test_break_lock(self):
        env = self.env
        env.agent("a")
        path = lock_path(env.cfg)
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "host": "another-host", "since": "now"}, handle)
        self.assertEqual(env.run("apply", "--yes")[0], 1)
        self.assertEqual(env.run("apply", "--yes", "--break-lock")[0], 0)


class Registration(unittest.TestCase):
    def make(self, **kwargs):
        env = Env(**kwargs)
        self.addCleanup(env.close)
        return env

    def reg(self, env, name):
        plan = engine.build_plan(env.ctx())
        return next(r for r in plan.registrations if r.name == name)

    def test_unregistered_crewmate_gets_create_command_and_dashboard_fields(self):
        env = self.make()
        env.agent("a", display_name="Agent A", triggers="when needed", reasoning_effort="high", starred=True)
        action = self.reg(env, "a")
        self.assertEqual(action.kind, "register")
        self.assertEqual(action.commands, [["agent", "create", "--name", "a", "--kiro-agent", "a", "--workspace", "default"]])
        self.assertEqual(action.dashboard["display_name"], "Agent A")
        self.assertEqual(action.dashboard["reasoning_effort"], "high")
        self.assertTrue(action.dashboard["starred"])

    def test_missing_workspace_is_created_first(self):
        env = self.make()
        env.agent("a", workspace="logs", workspace_dir="workspace-logs")
        action = self.reg(env, "a")
        self.assertEqual(action.commands[0], ["workspace", "create", "--name", "logs", "--dir", "workspace-logs"])
        self.assertEqual(action.commands[1][:3], ["agent", "create", "--name"])

    def test_existing_workspace_is_not_recreated(self):
        env = self.make(workspaces={"default": {"dir": "workspace"}, "logs": {"dir": "workspace-logs"}})
        env.agent("a", workspace="logs")
        action = self.reg(env, "a")
        self.assertEqual(len(action.commands), 1)

    def test_matching_record_is_in_sync(self):
        env = self.make(crew_agents={"default": {"kiro_agent": "kirocrew"},
                                     "a": {"kiro_agent": "a", "workspace": "default", "display_name": "Agent A",
                                           "description": "d", "triggers": "t", "reasoning_effort": "",
                                           "session_color": "", "starred": False, "model": ""}})
        env.agent("a", display_name="Agent A")
        action = self.reg(env, "a")
        self.assertEqual((action.kind, action.commands, action.dashboard), ("in-sync", [], {}))

    def test_differences_split_into_cli_and_dashboard(self):
        env = self.make(crew_agents={"default": {"kiro_agent": "kirocrew"},
                                     "a": {"kiro_agent": "old-file", "workspace": "default", "display_name": "",
                                           "description": "d", "triggers": "", "model": "glm-5"}})
        env.agent("a", display_name="Agent A")
        action = self.reg(env, "a")
        self.assertEqual(action.kind, "update")
        self.assertEqual(action.commands, [["agent", "update", "a", "--kiro-agent", "a"]])
        self.assertEqual(set(action.dashboard), {"display_name", "triggers"})
        self.assertTrue(any("pins model 'glm-5'" in n for n in action.notes))

    def test_removed_crewmate_with_leftover_record_is_flagged(self):
        env = self.make(crew_agents={"default": {"kiro_agent": "kirocrew"}, "a": {"kiro_agent": "a"}})
        env.agent("a")
        env.run("apply", "--yes")
        os.remove(os.path.join(env.cfg, "a.agent.json"))
        action = self.reg(env, "a")
        self.assertEqual((action.kind, action.commands), ("orphan", [["agent", "delete", "a"]]))

    def test_missing_crew_config_is_reported_not_fatal(self):
        env = self.make(with_crew_config=False)
        env.agent("a")
        action = self.reg(env, "a")
        self.assertEqual(action.kind, "unknown")
        code, text = env.run("plan")
        self.assertEqual(code, 0)
        self.assertIn("NOT FOUND", text)

    def test_tokens_in_crew_config_never_appear_in_output(self):
        env = self.make()
        env.agent("a")
        outputs = [env.run(*cmd)[1] for cmd in (("plan",), ("status",), ("register",), ("models",), ("validate",))]
        for text in outputs:
            self.assertNotIn("TOPSECRET", text)

    def test_status_table(self):
        env = self.make()
        env.agent("a")
        env.run("apply", "--yes")
        code, text = env.run("status")
        self.assertEqual(code, 0)
        self.assertIn("in sync", text)
        self.assertIn("not registered", text)

    def test_register_prints_by_default_and_never_runs(self):
        env = self.make()
        env.agent("a")
        env.run("apply", "--yes")
        marker = os.path.join(env.root, "ran")
        fake = self.fake_kirocrew(env, marker)
        env.settings_file(kirocrew_bin=fake)
        code, text = env.run("register")
        self.assertEqual(code, 0)
        self.assertIn("agent create --name a", text)
        self.assertFalse(os.path.exists(marker))

    def test_register_run_executes_the_commands_after_confirmation(self):
        env = self.make()
        env.agent("a", workspace="w1")
        env.run("apply", "--yes")
        marker = os.path.join(env.root, "ran")
        fake = self.fake_kirocrew(env, marker)
        env.settings_file(kirocrew_bin=fake)
        code, text = env.run("register", "--run")                 # no --yes, not a terminal
        self.assertFalse(os.path.exists(marker))
        code, text = env.run("register", "--run", "--yes")
        self.assertEqual(code, 0, text)
        with open(marker, encoding="utf-8") as handle:
            calls = [line.split() for line in handle.read().splitlines()]
        self.assertEqual(calls[0][:3], ["workspace", "create", "--name"])
        self.assertEqual(calls[1][:4], ["agent", "create", "--name", "a"])

    def test_register_run_stops_on_failure(self):
        env = self.make()
        env.agent("a")
        env.run("apply", "--yes")
        marker = os.path.join(env.root, "ran")
        fake = self.fake_kirocrew(env, marker, exit_code=3)
        env.settings_file(kirocrew_bin=fake)
        code, text = env.run("register", "--run", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("failed (exit 3)", text)

    @staticmethod
    def fake_kirocrew(env, marker, exit_code=0):
        path = os.path.join(env.root, "fake-kirocrew")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\necho \"$@\" >> '%s'\nexit %d\n" % (marker, exit_code))
        os.chmod(path, 0o755)
        return path


class Removal(unittest.TestCase):
    """Deleting one crewmate, or all of them: the agent file goes, and the Crew record is not forgotten."""

    def make(self):
        records = {"default": {"kiro_agent": "kirocrew"}, "a": {"kiro_agent": "a"}, "b": {"kiro_agent": "b"}}
        env = Env(crew_agents=records)
        self.addCleanup(env.close)
        env.agent("a")
        env.agent("b")
        self.assertEqual(env.run("apply", "--yes")[0], 0)
        self.marker = os.path.join(env.root, "ran")
        env.settings_file(plan="free", kirocrew_bin=Registration.fake_kirocrew(env, self.marker))
        return env

    def ran(self):
        if not os.path.exists(self.marker):
            return []
        with open(self.marker, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def test_delete_one_by_removing_its_config_then_apply_then_register(self):
        env = self.make()
        os.remove(os.path.join(env.cfg, "a.agent.json"))
        code, text = env.run("apply", "--yes")
        self.assertEqual(code, 0, text)
        self.assertFalse(os.path.exists(env.agent_file("a")))
        self.assertTrue(os.path.exists(env.agent_file("b")))
        self.assertIn("agent delete a", text)                            # the reminder appears right after apply
        self.assertIn("a", load_state(env.cfg).removed)
        code, text = env.run("register")                                 # and again later
        self.assertIn("agent delete a", text)
        self.assertEqual(self.ran(), [])                                 # printing never runs anything
        self.assertEqual(env.run("register", "--run", "--yes")[0], 0)
        self.assertEqual(self.ran(), ["agent delete a"])

    def test_destroy_target_removes_one_file_and_prints_the_record_command(self):
        env = self.make()
        code, text = env.run("destroy", "--yes", "--target", "a")
        self.assertEqual(code, 0, text)
        self.assertFalse(os.path.exists(env.agent_file("a")))
        self.assertTrue(os.path.exists(env.agent_file("b")))
        self.assertTrue(os.path.exists(os.path.join(env.cfg, "a.agent.json")))     # config kept
        self.assertIn("agent delete a", text)
        self.assertNotIn("agent delete b", text)
        self.assertEqual(self.ran(), [])

    def test_destroy_all_with_delete_records(self):
        env = self.make()
        code, text = env.run("destroy", "--yes", "--delete-records")
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(self.ran()), ["agent delete a", "agent delete b"])
        self.assertEqual(os.listdir(env.agents), [])

    def test_destroy_without_the_flag_never_runs_kirocrew(self):
        env = self.make()
        env.run("destroy", "--yes")
        self.assertEqual(self.ran(), [])

    def test_destroy_never_deletes_the_default_crewmate(self):
        env = self.make()
        code, text = env.run("destroy", "--yes", "--delete-records")
        self.assertNotIn("agent delete default", "\n".join(self.ran()) + text)

    def test_recreating_a_destroyed_crewmate_clears_the_reminder(self):
        env = self.make()
        env.run("destroy", "--yes", "--target", "a")
        self.assertIn("a", load_state(env.cfg).removed)
        self.assertEqual(env.run("apply", "--yes")[0], 0)                # the config is still there, so it comes back
        self.assertNotIn("a", load_state(env.cfg).removed)
        self.assertTrue(os.path.exists(env.agent_file("a")))

    def test_reminder_is_dropped_once_the_crew_record_is_gone(self):
        env = self.make()
        os.remove(os.path.join(env.cfg, "a.agent.json"))
        env.run("apply", "--yes")
        records = {"default": {"kiro_agent": "kirocrew"}, "b": {"kiro_agent": "b"}}      # a's record was deleted
        env.write_json(os.path.join(env.crew_home, "config.json"), {"agents": records, "workspaces": {}})
        env.agent("b", model="glm-5")
        env.run("apply", "--yes")
        self.assertEqual(load_state(env.cfg).removed, {})

    def test_state_rm_clears_a_reminder(self):
        env = self.make()
        os.remove(os.path.join(env.cfg, "a.agent.json"))
        env.run("apply", "--yes")
        self.assertEqual(env.run("state", "rm", "a")[0], 0)
        self.assertNotIn("agent delete a", env.run("register")[1])

    def test_a_state_file_from_before_this_feature_still_loads(self):
        env = self.make()
        path = os.path.join(env.cfg, ".crewctl", "state.json")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        data.pop("removed", None)
        env.write_json(path, data)
        self.assertEqual(env.run("plan", "--detailed-exitcode")[0], 0)

    def test_destroy_blocks_on_drift_unless_forced(self):
        env = self.make()
        with open(env.agent_file("a"), "a", encoding="utf-8") as handle:
            handle.write(" ")
        self.assertEqual(env.run("destroy", "--yes", "--target", "a")[0], 1)
        self.assertTrue(os.path.exists(env.agent_file("a")))
        self.assertEqual(env.run("destroy", "--yes", "--force", "--target", "a")[0], 0)
        self.assertFalse(os.path.exists(env.agent_file("a")))


class Capabilities(Base):
    """Skills, Crew's own MCP servers, and per-agent MCP servers."""

    def write_kirocrew_json(self, **servers):
        servers = servers or {
            "kirocrew-core": {"command": "/opt/KiroCrew/bin/kirocrew", "args": ["mcp-core"],
                              "env": {"SECRET_NAME": "do-not-copy"}, "autoApprove": ["*"]},
            "kirocrew-cron": {"command": "/opt/KiroCrew/bin/kirocrew", "args": ["mcp-cron"]},
        }
        self.env.write_json(os.path.join(self.env.agents, "kirocrew.json"), {"name": "kirocrew", "mcpServers": servers})

    def test_skills_become_skill_resources(self):
        env = self.env
        env.agent("a", skills=[".kiro/skills/log-triage/SKILL.md", "skill://~/.kiro/skills/*/SKILL.md"],
                  resources=["file://.kiro/steering/**/*.md"])
        self.assertEqual(env.run("apply", "--yes")[0], 0)
        self.assertEqual(env.read_agent_file("a")["resources"], [
            "file://.kiro/steering/**/*.md", "skill://.kiro/skills/log-triage/SKILL.md", "skill://~/.kiro/skills/*/SKILL.md"])

    def test_skills_do_not_leak_between_agents_through_defaults(self):
        env = self.env
        env.write_json(os.path.join(env.cfg, "_defaults.json"), {"resources": ["file://.kiro/steering/**/*.md"]})
        env.agent("a", skills=[".kiro/skills/x/SKILL.md"])
        env.agent("b")
        specs = {s.name: s for s in env.ctx().specs}
        self.assertEqual(specs["b"].resources, ["file://.kiro/steering/**/*.md"])
        self.assertEqual(len(specs["a"].resources), 2)

    def test_skills_must_be_a_list_of_text(self):
        self.env.agent("a", skills="log-triage")
        self.assertTrue(any("skills must be a list" in e for e in errors_of(self.env)))

    def test_crew_servers_are_looked_up_on_this_machine_and_only_command_and_args_are_copied(self):
        env = self.env
        self.write_kirocrew_json()
        env.agent("a", crew_servers=["core", "cron"], tools=["fs_read"])
        self.assertEqual(env.run("apply", "--yes")[0], 0)
        data = env.read_agent_file("a")
        self.assertEqual(data["mcpServers"]["kirocrew-core"], {"command": "/opt/KiroCrew/bin/kirocrew", "args": ["mcp-core"]})
        self.assertEqual(data["mcpServers"]["kirocrew-cron"]["args"], ["mcp-cron"])
        self.assertEqual(data["tools"], ["fs_read", "@kirocrew-core", "@kirocrew-cron"])
        self.assertNotIn("do-not-copy", json.dumps(data))
        self.assertNotIn("autoApprove", json.dumps(data))

    def test_crew_server_tool_is_not_duplicated_or_added_to_a_wildcard(self):
        self.write_kirocrew_json()
        self.env.agent("a", crew_servers=["core"], tools=["fs_read", "@kirocrew-core"])
        self.env.agent("b", crew_servers=["core"], tools=["*"])
        specs = {s.name: s for s in self.env.ctx().specs}
        self.assertEqual(specs["a"].tools, ["fs_read", "@kirocrew-core"])
        self.assertEqual(specs["b"].tools, ["*"])

    def test_disabled_tools_option_is_passed_through(self):
        env = self.env
        self.write_kirocrew_json()
        env.agent("a", crew_servers={"core": {"disabledTools": ["task_run", "workflow_run"]}})
        env.run("apply", "--yes")
        self.assertEqual(env.read_agent_file("a")["mcpServers"]["kirocrew-core"]["disabledTools"], ["task_run", "workflow_run"])

    def test_auto_approve_is_refused_for_crew_servers(self):
        self.write_kirocrew_json()
        self.env.agent("a", crew_servers={"core": {"autoApprove": ["*"]}})
        self.assertTrue(any("only \"tools\" and \"disabledTools\"" in e and "auto-approving" in e for e in errors_of(self.env)))

    def test_unknown_crew_server_name(self):
        self.write_kirocrew_json()
        self.env.agent("a", crew_servers=["dashboard"])
        self.assertTrue(any("unknown server 'dashboard'" in e for e in errors_of(self.env)))

    def test_missing_kirocrew_json_gives_a_helpful_error(self):
        self.env.agent("a", crew_servers=["core"])
        found = errors_of(self.env)
        self.assertTrue(any("cannot find 'kirocrew-core'" in e and "kirocrew.json" in e for e in found), found)

    def test_missing_entry_inside_kirocrew_json(self):
        self.write_kirocrew_json(**{"kirocrew-cron": {"command": "x"}})
        self.env.agent("a", crew_servers=["core"])
        self.assertTrue(any("cannot find 'kirocrew-core'" in e for e in errors_of(self.env)))

    def test_clash_with_a_hand_written_server_of_the_same_name(self):
        self.write_kirocrew_json()
        self.env.agent("a", crew_servers=["core"], mcp_servers={"kirocrew-core": {"command": "x"}})
        self.assertTrue(any("already defines 'kirocrew-core'" in e for e in errors_of(self.env)))

    def test_a_new_crew_install_path_shows_up_as_a_normal_update(self):
        env = self.env
        self.write_kirocrew_json()
        env.agent("a", crew_servers=["core"])
        env.run("apply", "--yes")
        self.write_kirocrew_json(**{"kirocrew-core": {"command": "/new/place/kirocrew", "args": ["mcp-core"]}})
        code, text = env.run("plan", "--detailed-exitcode")
        self.assertEqual(code, 2)
        self.assertIn("/new/place/kirocrew", text)
        self.assertNotIn("DRIFT", text)

    def test_per_agent_mcp_servers_keep_kiro_only_keys_and_env_placeholders(self):
        env = self.env
        env.agent("a", tools=["fs_read", "@metrics"], mcp_servers={
            "metrics": {"command": "metrics-mcp", "args": ["--read-only"], "env": {"TOKEN": "${METRICS_TOKEN}"},
                        "disabledTools": ["delete_series"], "autoApprove": ["query"], "timeout": 30000},
            "remote": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ${API_TOKEN}"}}})
        self.assertEqual(errors_of(env), [])
        env.run("apply", "--yes")
        data = env.read_agent_file("a")
        self.assertEqual(data["mcpServers"]["metrics"]["disabledTools"], ["delete_series"])
        self.assertEqual(data["mcpServers"]["metrics"]["env"]["TOKEN"], "${METRICS_TOKEN}")
        self.assertEqual(data["mcpServers"]["remote"]["headers"]["Authorization"], "Bearer ${API_TOKEN}")

    def test_crew_server_tools_option_allowlists_single_tools_instead_of_the_whole_server(self):
        env = self.env
        self.write_kirocrew_json()
        env.agent("a", tools=["fs_read"], crew_servers={"core": {"tools": ["spawn_run", "send_message"]}})
        self.assertEqual(errors_of(env), [])
        env.run("apply", "--yes")
        data = env.read_agent_file("a")
        self.assertEqual(data["tools"], ["fs_read", "@kirocrew-core/spawn_run", "@kirocrew-core/send_message"])
        self.assertNotIn("@kirocrew-core", data["tools"])
        self.assertIn("kirocrew-core", data["mcpServers"])
        self.assertNotIn("tools", data["mcpServers"]["kirocrew-core"])

    def test_crew_server_tools_option_validates_names(self):
        self.write_kirocrew_json()
        self.env.agent("a", crew_servers={"core": {"tools": ["spawn run"]}})
        self.assertTrue(any("plain tool names" in e for e in errors_of(self.env)))
        self.env.agent("b", crew_servers={"core": {"tools": "spawn_run"}})
        self.assertTrue(any("only \"tools\" and \"disabledTools\"" in e for e in errors_of(self.env)))

    def test_hooks_accept_both_kiro_styles_and_reject_junk(self):
        env = self.env
        legacy = {"preToolUse": [{"matcher": "read", "command": "echo hi"}]}
        v3 = [{"name": "probe", "trigger": "PreToolUse", "matcher": "read", "action": {"type": "command", "command": "cat"}}]
        env.agent("a", hooks=legacy)
        env.agent("b", hooks=v3)
        self.assertEqual(errors_of(env), [])
        env.run("apply", "--yes")
        self.assertEqual(env.read_agent_file("a")["hooks"], legacy)
        self.assertEqual(env.read_agent_file("b")["hooks"], v3)
        env.agent("c", hooks="echo hi")
        self.assertTrue(any("hooks must be" in e for e in errors_of(env)))

    def test_include_mcp_json_defaults_to_false_and_can_be_turned_on(self):
        env = self.env
        env.agent("a")
        env.agent("b", include_mcp_json=True)
        env.run("apply", "--yes")
        self.assertFalse(env.read_agent_file("a")["includeMcpJson"])
        self.assertTrue(env.read_agent_file("b")["includeMcpJson"])


class InitAndExamples(unittest.TestCase):
    def test_init_creates_a_usable_folder(self):
        env = Env()
        self.addCleanup(env.close)
        target = os.path.join(env.root, "fresh")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["-c", target, "init"])
        self.assertEqual(code, 0)
        for name in ("crewctl.json", "_defaults.json", "_template.agent.json", ".gitignore", "prompts/.gitkeep"):
            self.assertTrue(os.path.exists(os.path.join(target, name)), name)
        with open(os.path.join(target, ".gitignore"), encoding="utf-8") as handle:
            self.assertIn(".crewctl/", handle.read())
        ctx = engine.build_context(target, {"agents_dir": env.agents, "crew_home": env.crew_home})
        self.assertEqual([p for p in ctx.problems if p.level == "error"], [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["-c", target, "init"]), 1)           # refuses to overwrite

    def test_template_fields_all_pass_validation_once_copied(self):
        env = Env()
        self.addCleanup(env.close)
        with open(os.path.join(ROOT, "templates", "agent.template.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        with open(os.path.join(env.cfg, "prompts", "my-crewmate.md"), "w", encoding="utf-8") as handle:
            handle.write("Prompt.")
        env.write_json(os.path.join(env.cfg, "my-crewmate.agent.json"), data)
        self.assertEqual(errors_of(env), [])
        self.assertTrue(set(k for k in data if not k.startswith("_")) <= set(config.AGENT_KEYS))

    def test_shipped_examples_are_valid_on_the_free_plan(self):
        env = Env()
        self.addCleanup(env.close)
        ctx = engine.build_context(os.path.join(ROOT, "examples", "crew"),
                                   {"agents_dir": env.agents, "crew_home": env.crew_home})
        self.assertEqual([str(p) for p in ctx.problems if p.level == "error"], [])
        self.assertEqual(sorted(s.name for s in ctx.specs), ["change-reviewer", "conductor", "log-analyst"])
        plan = engine.build_plan(ctx)
        self.assertEqual(plan.counts(), {"create": 3})


class RealHomeIsUntouched(unittest.TestCase):
    def test_default_paths_follow_home(self):
        env = Env()
        self.addCleanup(env.close)
        self.assertTrue(config.default_agents_dir().startswith(env.home))
        self.assertTrue(config.default_crew_home().startswith(env.home))



class SkillLint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def skill(self, folder, text):
        path = os.path.join(self.tmp, folder)
        os.makedirs(path, exist_ok=True)
        file = os.path.join(path, "SKILL.md")
        with open(file, "w", encoding="utf-8") as handle:
            handle.write(text)
        return file

    def levels(self, file):
        from crewctl.skilllint import lint_file
        return [(f.level, f.message) for f in lint_file(file)]

    GOOD = "---\nname: good-one\ndescription: Use when checking things. Produces a list.\ntriggers: a b, c d, e f\n---\n# Body\n"

    def test_good_skill_is_clean(self):
        self.assertEqual(self.levels(self.skill("good-one", self.GOOD)), [])

    def test_name_must_match_folder_and_shape(self):
        found = self.levels(self.skill("other", self.GOOD))
        self.assertTrue(any(l == "error" and "must equal the folder" in m for l, m in found))
        bad = self.GOOD.replace("good-one", "Bad_Name")
        found = self.levels(self.skill("Bad_Name", bad))
        self.assertTrue(any("lowercase" in m for _, m in found))

    def test_missing_frontmatter_and_description(self):
        self.assertTrue(any("no frontmatter" in m for _, m in self.levels(self.skill("a", "# just text"))))
        text = "---\nname: b\n---\n"
        self.assertTrue(any("missing 'description'" in m for _, m in self.levels(self.skill("b", text))))

    def test_unclosed_frontmatter(self):
        text = "---\nname: c\ndescription: Use when x.\n"
        self.assertTrue(any("not closed" in m for _, m in self.levels(self.skill("c", text))))

    def test_warnings(self):
        text = "---\nname: d\ndescription: Does things: fast\nalways: true\n---\n"
        messages = " | ".join(m for _, m in self.levels(self.skill("d", text)))
        for part in ("'Use when", "': '", "triggers", "always: true"):
            self.assertIn(part, messages)

    def test_size_and_secret(self):
        big = self.GOOD + "x" * 25_000
        self.assertTrue(any(l == "error" and "bytes" in m for l, m in self.levels(self.skill("good-one", big))))
        leaked = self.GOOD + "token sk-ant-" + "a" * 40 + "\n"
        self.assertTrue(any("credential" in m for _, m in self.levels(self.skill("good-one", leaked))))

    def test_shipped_templates_and_examples_are_clean(self):
        from crewctl.skilllint import lint_paths
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files, findings = lint_paths([os.path.join(root, "templates", "skill-template"), os.path.join(root, "examples", "skills")])
        self.assertEqual(len(files), 4)
        self.assertEqual(findings, [])

    def test_cli_exit_code(self):
        self.skill("good-one", self.GOOD)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--no-color", "lint-skills", self.tmp]), 0)
            self.skill("bad", "no frontmatter")
            self.assertEqual(cli.main(["--no-color", "lint-skills", self.tmp]), 1)


class SkillGeneration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = os.path.join(self.tmp, "crew")
        self.home = os.path.join(self.tmp, "home")
        self.project = os.path.join(self.tmp, "repo", ".kiro", "skills")
        os.makedirs(os.path.join(self.config, "skills"))
        os.makedirs(os.path.join(self.home, "skills"))
        self.write("crewctl.json", {"crew_home": self.home, "project_skills_dir": "../repo/.kiro/skills"})

    def write(self, rel, data):
        with open(os.path.join(self.config, rel), "w", encoding="utf-8") as handle:
            handle.write(data if isinstance(data, str) else json.dumps(data))

    def spec(self, name="demo", **extra):
        data = {"name": name, "description": "Use when testing. Makes a list.", "triggers": ["a b", "c d", "e f"], "steps": ["one", "two"]}
        data.update(extra)
        self.write(f"skills/{name}.skill.json", data)

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--no-color", "-c", self.config, "skills", *argv])
        return code, buffer.getvalue()

    def skill_file(self, name="demo", base=None):
        return os.path.join(base or os.path.join(self.home, "skills"), name, "SKILL.md")

    def test_create_noop_update_destroy(self):
        self.spec()
        code, text = self.run_cli("apply", "-y")
        self.assertEqual(code, 0, text)
        content = open(self.skill_file(), encoding="utf-8").read()
        self.assertTrue(content.startswith("---\nname: demo\ndescription: Use when testing. Makes a list.\ntriggers: a b, c d, e f\n---\n"))
        self.assertIn("1. one\n2. two", content)
        self.assertEqual(oct(os.stat(self.skill_file()).st_mode & 0o777), "0o600")
        code, text = self.run_cli("apply", "-y")
        self.assertIn("No changes", text)
        self.spec(steps=["one", "two", "three"])
        code, text = self.run_cli("apply", "-y")
        self.assertIn("1 updated", text)
        self.assertIn("3. three", open(self.skill_file(), encoding="utf-8").read())
        backups = os.path.join(self.config, ".crewctl", "backups")
        self.assertTrue(any("skill-demo--SKILL.md" in files for _, _, files in os.walk(backups)))
        code, text = self.run_cli("destroy", "-y")
        self.assertEqual(code, 0, text)
        self.assertFalse(os.path.exists(os.path.dirname(self.skill_file())))

    def test_never_overwrites_a_folder_it_did_not_create(self):
        os.makedirs(os.path.dirname(self.skill_file()))
        open(self.skill_file(), "w").write("mine")
        self.spec()
        code, text = self.run_cli("apply", "-y", "--force")
        self.assertEqual(code, 1)
        self.assertEqual(open(self.skill_file()).read(), "mine")

    def test_drift_needs_force_even_when_content_matches_the_spec(self):
        self.spec()
        self.run_cli("apply", "-y")
        with open(self.skill_file(), "a") as handle:
            handle.write("edit\n")
        code, text = self.run_cli("apply", "-y")
        self.assertEqual(code, 1)
        self.assertIn("changed outside crewctl", text)
        self.assertIn("edit", open(self.skill_file()).read())
        code, _ = self.run_cli("apply", "-y", "--force")
        self.assertEqual(code, 0)
        self.assertNotIn("edit", open(self.skill_file()).read())

    def test_removed_spec_deletes_only_what_crewctl_wrote(self):
        self.spec()
        self.run_cli("apply", "-y")
        extra = os.path.join(os.path.dirname(self.skill_file()), "notes.txt")
        open(extra, "w").write("keep")
        os.unlink(os.path.join(self.config, "skills", "demo.skill.json"))
        code, text = self.run_cli("apply", "-y")
        self.assertEqual(code, 0, text)
        self.assertFalse(os.path.exists(self.skill_file()))
        self.assertTrue(os.path.exists(extra))

    def test_project_destination_and_extra_files(self):
        self.write("ref.md", "# reference\n")
        self.spec(destination="project", files={"references/ref.md": "ref.md"})
        code, text = self.run_cli("apply", "-y")
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.exists(self.skill_file(base=self.project)))
        self.assertEqual(open(os.path.join(self.project, "demo", "references", "ref.md")).read(), "# reference\n")

    def test_validation_errors(self):
        self.spec(name="Bad_Name")
        code, text = self.run_cli("plan")
        self.assertEqual(code, 1)
        self.assertIn("lowercase", text)
        os.unlink(os.path.join(self.config, "skills", "Bad_Name.skill.json"))
        for extra, expected in (({"steps": []}, "needs 'steps'"), ({"bogus": 1}, "unknown field"),
                                ({"destination": "elsewhere"}, "destination must be"),
                                ({"files": {"../x": "ref.md"}}, "plain relative path"),
                                ({"triggers": ["a, b"]}, "comma"), ({"body_file": "../outside.md"}, "outside the config folder")):
            self.spec(**extra)
            code, text = self.run_cli("plan")
            self.assertEqual(code, 1, (extra, text))
            self.assertIn(expected, text, extra)

    def test_lint_errors_block_apply(self):
        self.spec(description="Use when x. token sk-ant-" + "a" * 40)
        code, text = self.run_cli("apply", "-y")
        self.assertEqual(code, 1)
        self.assertIn("credential", text)
        self.assertFalse(os.path.exists(self.skill_file()))

    def test_bundled_script_is_executable_and_skill_dir_is_filled_in(self):
        script = os.path.join(self.config, "tool.py")
        open(script, "w").write("print('hi')\n")
        os.chmod(script, 0o755)
        plain = os.path.join(self.config, "notes.md")
        open(plain, "w").write("notes\n")
        self.spec(steps=["Run `python3 {{skill_dir}}/scripts/tool.py`."],
                  files={"scripts/tool.py": "tool.py", "references/notes.md": "notes.md"})
        code, text = self.run_cli("apply", "-y")
        self.assertEqual(code, 0, text)
        folder = os.path.dirname(self.skill_file())
        self.assertIn(f"python3 {folder}/scripts/tool.py", open(self.skill_file()).read())
        self.assertTrue(os.access(os.path.join(folder, "scripts", "tool.py"), os.X_OK))
        self.assertFalse(os.access(os.path.join(folder, "references", "notes.md"), os.X_OK))
        # flipping the permission in the spec is an update even though the content is identical
        self.spec(steps=["Run `python3 {{skill_dir}}/scripts/tool.py`."],
                  files={"scripts/tool.py": {"source": "tool.py", "executable": False}, "references/notes.md": "notes.md"})
        code, text = self.run_cli("apply", "-y")
        self.assertIn("1 updated", text)
        self.assertFalse(os.access(os.path.join(folder, "scripts", "tool.py"), os.X_OK))

    def test_lint_flags_missing_script_and_binary(self):
        from crewctl.skilllint import lint_file
        folder = os.path.join(self.tmp, "lintme")
        os.makedirs(os.path.join(folder, "scripts"))
        with open(os.path.join(folder, "SKILL.md"), "w") as handle:
            handle.write("---\nname: lintme\ndescription: Use when x.\ntriggers: a b, c d, e f\n---\nRun scripts/missing.py\n")
        with open(os.path.join(folder, "scripts", "tool"), "wb") as handle:
            handle.write(b"\x7fELF\xff\xfe\x00")
        messages = " | ".join(f.message for f in lint_file(os.path.join(folder, "SKILL.md")))
        self.assertIn("scripts/missing.py, which is not in the skill folder", messages)
        self.assertIn("binary file", messages)

    def test_description_with_colon_is_quoted(self):
        self.spec(description="Use when checking: things")
        self.run_cli("apply", "-y")
        self.assertIn('description: "Use when checking: things"', open(self.skill_file()).read())

    def test_new_creates_a_spec_that_fails_until_filled_in_and_init_adds_template(self):
        code, text = self.run_cli("new", "my-skill")
        self.assertEqual(code, 0, text)
        data = json.load(open(os.path.join(self.config, "skills", "my-skill.skill.json")))
        self.assertEqual(data["name"], "my-skill")
        self.assertEqual(self.run_cli("new", "my-skill")[0], 1)
        self.assertEqual(self.run_cli("new", "Bad Name")[0], 1)
        fresh = os.path.join(self.tmp, "fresh")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["-c", fresh, "init"])
        self.assertTrue(os.path.exists(os.path.join(fresh, "skills", "_template.skill.json")))

    def test_shipped_example_spec_builds_a_clean_skill(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        from crewctl.skillgen import load_skill_specs, rendered_files, lint_rendered
        settings, specs, problems = load_skill_specs(os.path.join(root, "examples", "crew"))
        self.assertEqual([p for p in problems if p.level == "error"], [])
        self.assertEqual([s.name for s in specs], ["iac-security-review"])
        files = rendered_files(specs[0], "/x/iac-security-review")
        self.assertIn("scripts/changed_iac.py", files)
        self.assertIn("python3 /x/iac-security-review/scripts/changed_iac.py", files["SKILL.md"].decode())
        self.assertEqual(lint_rendered(specs[0], files), [])

if __name__ == "__main__":
    unittest.main()
