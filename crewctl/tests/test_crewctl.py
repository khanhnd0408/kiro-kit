"""Tests for crewctl. Every test works in temp folders with HOME pointed at a temp folder, so
nothing here can touch a real ~/.kiro. The only program ever executed is a fake `kirocrew` script
that the tests write themselves.

Run:  python3 -m unittest discover -s tests -v     (from the crewctl folder)
"""

import argparse
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
        self.assertEqual(action.commands[0], ["agent", "create", "--name", "a", "--kiro-agent", "a", "--workspace", "default"])
        # the rest is written by Crew's own CLI, with every value JSON-encoded so text stays text
        self.assertIn(["config", "set", "agents.a.display_name", '"Agent A"'], action.commands)
        self.assertIn(["config", "set", "agents.a.triggers", '"when needed"'], action.commands)
        self.assertIn(["config", "set", "agents.a.reasoning_effort", '"high"'], action.commands)
        self.assertIn(["config", "set", "agents.a.starred", "true"], action.commands)
        self.assertEqual(action.dashboard, {})

    def test_text_that_looks_like_a_number_or_boolean_is_written_as_text(self):
        env = self.make()
        env.agent("a", description="true", triggers="123")
        action = self.reg(env, "a")
        self.assertIn(["config", "set", "agents.a.description", '"true"'], action.commands)
        self.assertIn(["config", "set", "agents.a.triggers", '"123"'], action.commands)

    def test_name_with_a_dot_is_left_for_the_dashboard(self):
        commands, left = engine.config_set_commands("v1.agent", {"description": "d", "starred": True, "other": 1})
        self.assertEqual(commands, [])
        self.assertEqual(left, {"description": "d", "starred": True, "other": 1})

    def test_missing_workspace_is_an_error_and_register_never_creates_one(self):
        env = self.make()
        env.agent("a", workspace="logs", workspace_dir="workspace-logs")
        for command in ("validate", "plan", "register"):
            code, text = env.run(command)
            self.assertEqual(code, 1, (command, text))
            self.assertIn("workspace 'logs' does not exist in Crew", text)
            self.assertIn("crewctl workspace check", text)
        self.assertEqual(engine.workspace_problems(env.ctx().specs, env.ctx().view)[0].level, "error")

    def test_existing_workspace_is_not_recreated(self):
        env = self.make(workspaces={"default": {"dir": "workspace"}, "logs": {"dir": "workspace-logs"}})
        env.agent("a", workspace="logs")
        action = self.reg(env, "a")
        self.assertEqual(action.commands[0][:2], ["agent", "create"])
        self.assertNotIn(["workspace", "create"], [c[:2] for c in action.commands])
        self.assertEqual(engine.workspace_problems(env.ctx().specs, env.ctx().view), [])

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
        self.assertEqual(action.commands, [["agent", "update", "a", "--kiro-agent", "a"],
                                           ["config", "set", "agents.a.display_name", '"Agent A"'],
                                           ["config", "set", "agents.a.triggers", '"t"']])
        self.assertEqual(action.dashboard, {})
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
        env.agent("a")
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
        self.assertEqual(calls[0][:4], ["agent", "create", "--name", "a"])
        self.assertTrue(all(call[0] != "workspace" for call in calls))

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
        self.assertEqual(self.ran(), ['config set agents.b.description "d"', 'config set agents.b.triggers "t"', "agent delete a"])

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

    def other_config_folder(self):
        """A second config folder with the same skill specs, as when a team is copied or the folder changes."""
        other = os.path.join(self.tmp, "crew2")
        shutil.copytree(self.config, other, ignore=shutil.ignore_patterns(".crewctl"))
        return other

    def run_other(self, other, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--no-color", "-c", other, "skills", *argv])
        return code, buffer.getvalue()

    def test_a_folder_that_is_identical_is_adopted_without_writing_anything(self):
        self.spec()
        self.run_cli("apply", "-y")
        before = os.stat(self.skill_file()).st_mtime_ns
        other = self.other_config_folder()                      # same specs, but no state: crewctl has no record
        code, text = self.run_other(other, "apply", "-y")
        self.assertEqual(code, 0, text)
        self.assertIn("1 adopted", text)
        self.assertEqual(os.stat(self.skill_file()).st_mtime_ns, before)
        self.assertEqual(self.run_other(other, "apply", "-y")[1].count("No changes"), 1)       # recorded: now a no-op

    def test_a_different_folder_needs_adopt_explains_why_and_is_backed_up(self):
        self.spec()
        self.run_cli("apply", "-y")
        extra = os.path.join(os.path.dirname(self.skill_file()), "notes.txt")
        open(extra, "w").write("mine")
        self.spec(steps=["one", "two", "three"])
        other = self.other_config_folder()
        code, text = self.run_other(other, "plan")
        self.assertEqual(code, 0)
        self.assertIn("needs --adopt", text)
        code, text = self.run_other(other, "apply", "-y")
        self.assertEqual(code, 1)
        self.assertIn("no record of creating it in this config folder", text)
        self.assertIn("per config folder", text)
        self.assertNotIn("three", open(self.skill_file()).read())
        code, text = self.run_other(other, "apply", "-y", "--force")                 # --force is not enough
        self.assertEqual(code, 1)
        code, text = self.run_other(other, "apply", "-y", "--adopt")
        self.assertEqual(code, 0, text)
        self.assertIn("3. three", open(self.skill_file()).read())
        self.assertEqual(open(extra).read(), "mine")
        backups = os.path.join(other, ".crewctl", "backups")
        self.assertTrue(any("SKILL.md" in f for _, _, files in os.walk(backups) for f in files))

    def test_crews_builtin_skills_are_never_adopted(self):
        self.spec()
        folder = os.path.dirname(self.skill_file())
        os.makedirs(folder)
        open(self.skill_file(), "w").write("built-in text")
        open(os.path.join(folder, ".builtin-skill-provenance"), "w").write("2:abc\n")
        for flags in ((), ("--adopt",), ("--adopt", "--force")):
            code, text = self.run_cli("apply", "-y", *flags)
            self.assertEqual(code, 1, flags)
            self.assertIn("one of Crew's built-in skills", text)
        self.assertEqual(open(self.skill_file()).read(), "built-in text")

    def test_adopt_keeps_the_execute_permission_of_a_bundled_script(self):
        script = os.path.join(self.config, "tool.py")
        open(script, "w").write("print('hi')\n")
        os.chmod(script, 0o755)
        self.spec(files={"scripts/tool.py": "tool.py"})
        self.run_cli("apply", "-y")
        installed = os.path.join(os.path.dirname(self.skill_file()), "scripts", "tool.py")
        os.chmod(installed, 0o600)                                              # permission lost, content identical
        code, text = self.run_other(self.other_config_folder(), "apply", "-y")
        self.assertEqual(code, 0, text)
        self.assertTrue(os.access(installed, os.X_OK))

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


class CrewServerFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.agents = os.path.join(self.tmp, "agents")
        self.config = os.path.join(self.tmp, "crew")
        os.makedirs(self.agents)
        os.makedirs(self.config)

    def write_settings(self, data):
        with open(os.path.join(self.config, "crewctl.json"), "w") as handle:
            json.dump(data, handle)

    def agent(self):
        with open(os.path.join(self.config, "a.agent.json"), "w") as handle:
            json.dump({"name": "a", "prompt": "p", "tools": ["fs_read"], "crew_servers": {"core": {"tools": ["spawn_run"]}}}, handle)

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--no-color", "-c", self.config, "--agents-dir", self.agents, "--crew-home", self.tmp, *argv])
        return code, buffer.getvalue()

    def test_missing_file_is_an_error_that_points_to_the_diagnostic(self):
        self.agent()
        code, out = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertIn("crew-servers", out)

    def test_diagnostic_prints_a_snippet_when_missing(self):
        code, out = self.run_cli("crew-servers")
        self.assertEqual(code, 1)
        self.assertIn("NOT FOUND", out)
        self.assertIn('"crew_server_commands"', out)
        self.assertIn("mcp-core", out)
        self.assertIn("kiro_crew", out)

    def test_setting_replaces_the_missing_file(self):
        self.agent()
        self.write_settings({"crew_server_commands": {"core": {"command": "/opt/x/kirocrew", "args": ["mcp-core"]}}})
        code, out = self.run_cli("apply", "-y")
        self.assertEqual(code, 0, out)
        written = json.load(open(os.path.join(self.agents, "a.json")))
        self.assertEqual(written["mcpServers"]["kirocrew-core"], {"command": "/opt/x/kirocrew", "args": ["mcp-core"]})
        code, out = self.run_cli("crew-servers")
        self.assertIn("found from crewctl.json", out)

    def test_setting_wins_over_the_file(self):
        with open(os.path.join(self.agents, "kirocrew.json"), "w") as handle:
            json.dump({"mcpServers": {"kirocrew-core": {"command": "/from/file", "args": ["mcp-core"]}}}, handle)
        self.write_settings({"crew_server_commands": {"kirocrew-core": {"command": "/from/setting", "args": ["x"]}}})
        self.agent()
        self.run_cli("apply", "-y")
        written = json.load(open(os.path.join(self.agents, "a.json")))
        self.assertEqual(written["mcpServers"]["kirocrew-core"]["command"], "/from/setting")

    def test_crew_servers_can_be_switched_off(self):
        with open(os.path.join(self.config, "a.agent.json"), "w") as handle:
            json.dump({"name": "a", "prompt": "p", "tools": ["fs_read", "@kirocrew-core/skill_search"],
                       "allowed_tools": ["fs_read", "@kirocrew-core/skill_search"],
                       "crew_servers": {"core": {"tools": ["spawn_run", "wait"]}}, "skills": ["~/s/SKILL.md"]}, handle)
        self.write_settings({"use_crew_servers": False})
        code, out = self.run_cli("apply", "-y")
        self.assertEqual(code, 0, out)
        written = json.load(open(os.path.join(self.agents, "a.json")))
        self.assertEqual(written["tools"], ["fs_read"])
        self.assertEqual(written["allowedTools"], ["fs_read"])
        self.assertNotIn("kirocrew-core", written.get("mcpServers", {}))
        self.assertIn("skill://~/s/SKILL.md", written["resources"])
        self.assertIn("cannot start or watch other crewmates", out)

    def test_use_crew_servers_must_be_a_boolean(self):
        self.write_settings({"use_crew_servers": "no"})
        code, out = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertIn("use_crew_servers", out)

    def test_bad_setting_is_rejected(self):
        for bad in ("text", {"nope": {"command": "x", "args": []}}, {"core": {"command": "", "args": []}},
                    {"core": {"command": "x", "args": "mcp-core"}}, {"core": {"command": "x", "args": [], "env": {"A": "b"}}}):
            self.write_settings({"crew_server_commands": bad})
            code, out = self.run_cli("validate")
            self.assertEqual(code, 1, bad)
            self.assertIn("crew_server_commands", out, bad)


class AdoptAgentFiles(unittest.TestCase):
    """A second config folder has no state of its own; files that already match are adopted, others still conflict."""

    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)
        self.env.agent("a")
        self.assertEqual(self.env.run("apply", "--yes")[0], 0)
        self.other = os.path.join(self.env.root, "crew2")
        shutil.copytree(self.env.cfg, self.other, ignore=shutil.ignore_patterns(".crewctl"))

    def run_other(self, *argv):
        buffer = io.StringIO()
        base = ["-c", self.other, "--agents-dir", self.env.agents, "--crew-home", self.env.crew_home, "--no-color"]
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = cli.main(base + list(argv))
        return code, buffer.getvalue()

    def test_identical_file_from_another_config_folder_is_adopted_not_a_conflict(self):
        path = self.env.agent_file("a")
        before = os.stat(path).st_mtime_ns
        code, text = self.run_other("plan")
        self.assertEqual(code, 0, text)
        self.assertIn("adopt", text)
        self.assertNotIn("CONFLICT", text)
        code, text = self.run_other("apply", "--yes")
        self.assertEqual(code, 0, text)
        self.assertIn("1 adopted", text)
        self.assertEqual(os.stat(path).st_mtime_ns, before)                    # nothing was written
        self.assertIn("No changes", self.run_other("plan")[1])

    def test_a_different_file_is_still_a_conflict_and_the_message_explains_the_per_folder_state(self):
        with open(self.env.agent_file("a"), "a") as handle:
            handle.write("\n")
        code, text = self.run_other("apply", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("state has no record", text)
        self.assertIn("kept per config folder", text)
        self.assertIn("crewctl import a", text)


FAKE_MCP_SERVER = """
import json, os, sys
print("starting up (a log line, not JSON-RPC)", flush=True)
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "exit":
    sys.stderr.write("boom: missing API key " + os.environ.get("SECRET_VALUE", "")); sys.exit(3)
tools = [{"name": "alpha", "description": "First   tool\\nwith  spaces"}, {"name": "beta", "description": "Second tool"}, {"name": "gamma"}]
for line in sys.stdin:
    message = json.loads(line)
    method, ident = message.get("method"), message.get("id")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                                                                    "serverInfo": {"name": "fake", "version": "1"}}}), flush=True)
    elif method == "tools/list":
        cursor = (message.get("params") or {}).get("cursor")
        if mode == "hang":
            continue
        page = tools[:2] if not cursor else tools[2:]
        result = {"tools": page}
        if not cursor:
            result["nextCursor"] = "page2"
        print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)
"""


class McpCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.server = os.path.join(self.tmp, "fake_server.py")
        open(self.server, "w").write(FAKE_MCP_SERVER)
        self.mcp = os.path.join(self.tmp, "mcp.json")
        self.write_mcp({"fake": {"command": sys.executable, "args": [self.server], "env": {"SECRET_VALUE": "hunter2hunter2"}},
                        "off": {"command": sys.executable, "args": [self.server], "disabled": True},
                        "remote": {"url": "http://127.0.0.1:1/mcp", "headers": {"Authorization": "Bearer ${TOKEN_FOR_TEST}"}}})
        self.config = os.path.join(self.tmp, "crew")
        os.makedirs(self.config)
        self.agents = os.path.join(self.tmp, "agents")
        os.makedirs(self.agents)
        patch = mock.patch.dict(os.environ, {"CREWCTL_MCP_JSON": self.mcp, "CREWCTL_DIR": self.config}, clear=False)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("TOKEN_FOR_TEST", None)

    def write_mcp(self, servers):
        json.dump({"mcpServers": servers}, open(self.mcp, "w"))

    def agent(self, **fields):
        data = {"name": "a", "prompt": "p", "tools": ["fs_read"], "description": "d", "triggers": "t"}
        data.update(fields)
        json.dump(data, open(os.path.join(self.config, "a.agent.json"), "w"))

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = cli.main(["--no-color", "--agents-dir", self.agents, "--crew-home", self.tmp, *argv])
        return code, buffer.getvalue()

    # ---- the agent field
    def test_agent_names_a_server_from_mcp_json_and_nothing_is_copied(self):
        self.agent(mcp_json_servers=["fake"])
        code, text = self.run_cli("apply", "-y")
        self.assertEqual(code, 0, text)
        written = json.load(open(os.path.join(self.agents, "a.json")))
        self.assertTrue(written["includeMcpJson"])
        self.assertIn("@fake", written["tools"])
        self.assertNotIn("fake", written["mcpServers"])                    # no definition, no env, no secret copied
        self.assertNotIn("hunter2hunter2", json.dumps(written))

    def test_only_some_tools_of_a_server(self):
        self.agent(mcp_json_servers={"fake": {"tools": ["alpha", "beta"]}})
        self.run_cli("apply", "-y")
        written = json.load(open(os.path.join(self.agents, "a.json")))
        self.assertIn("@fake/alpha", written["tools"])
        self.assertIn("@fake/beta", written["tools"])
        self.assertNotIn("@fake", written["tools"])

    def test_defaults_file_with_include_mcp_json_false_does_not_block_it(self):
        json.dump({"include_mcp_json": False}, open(os.path.join(self.config, "_defaults.json"), "w"))
        self.agent(mcp_json_servers=["fake"])
        code, text = self.run_cli("validate")
        self.assertEqual(code, 0, text)

    def test_agent_field_errors(self):
        cases = ((["nope"], "'nope' is not in"), (["off"], "is disabled in"), ("fake", "must be a list of server names"),
                 ({"fake": {"tools": "alpha"}}, "must be {}"), ({"fake": {"tools": ["a b"]}}, "must be {}"),
                 ({"fake": {"surprise": 1}}, "must be {}"), (["bad name"], "not a valid server name"))
        for value, message in cases:
            self.agent(mcp_json_servers=value)
            code, text = self.run_cli("validate")
            self.assertEqual(code, 1, value)
            self.assertIn(message, text, value)
        self.agent(mcp_json_servers=["fake"], include_mcp_json=False)
        self.assertIn("needs include_mcp_json to be true", self.run_cli("validate")[1])
        self.agent(mcp_json_servers=["fake"], mcp_servers={"fake": {"command": "x"}})
        self.assertIn("in both mcp_servers and mcp_json_servers", self.run_cli("validate")[1])

    def test_missing_mcp_file_is_an_error_that_says_where_to_set_it(self):
        os.remove(self.mcp)
        self.agent(mcp_json_servers=["fake"])
        code, text = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertIn("no MCP file at", text)
        self.assertIn("CREWCTL_MCP_JSON", text)

    def test_mcp_json_setting_in_crewctl_json(self):
        elsewhere = os.path.join(self.tmp, "other-mcp.json")
        json.dump({"mcpServers": {"only-here": {"command": "x"}}}, open(elsewhere, "w"))
        json.dump({"mcp_json": elsewhere}, open(os.path.join(self.config, "crewctl.json"), "w"))
        with mock.patch.dict(os.environ, {"CREWCTL_MCP_JSON": ""}):
            self.agent(mcp_json_servers=["only-here"])
            code, text = self.run_cli("validate")
        self.assertEqual(code, 0, text)

    # ---- crewctl mcp list / tools
    def test_list_shows_servers_state_and_who_uses_them_without_secrets(self):
        self.agent(mcp_json_servers=["fake"])
        code, text = self.run_cli("mcp", "list")
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r"fake\s+command\s+enabled\s+a")
        self.assertRegex(text, r"off\s+command\s+disabled")
        self.assertRegex(text, r"remote\s+url\s+enabled")
        self.assertNotIn("hunter2hunter2", text)
        self.assertNotIn("TOKEN_FOR_TEST", text)

    # ---- crewctl mcp add / remove
    def agents_world(self, *names, **fields):
        for name in names:
            data = {"name": name, "prompt": "p", "tools": ["fs_read"], "description": "d", "triggers": "t"}
            data.update(fields.get(name, {}))
            json.dump(data, open(os.path.join(self.config, f"{name}.agent.json"), "w"), indent=2)

    def config_of(self, name):
        return json.load(open(os.path.join(self.config, f"{name}.agent.json")))

    def test_add_gives_several_crewmates_a_server_in_one_command(self):
        self.agents_world("a", "b", "c")
        code, text = self.run_cli("mcp", "add", "fake", "a", "b", "-y")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.config_of("a")["mcp_json_servers"], ["fake"])
        self.assertEqual(self.config_of("b")["mcp_json_servers"], ["fake"])
        self.assertNotIn("mcp_json_servers", self.config_of("c"))
        self.assertIn("`register` is not needed", text)
        backups = os.path.join(self.config, ".crewctl", "backups")
        self.assertEqual(sorted(f for _, _, fs in os.walk(backups) for f in fs), ["a.agent.json", "b.agent.json"])
        self.assertEqual(list(self.config_of("a"))[:3], ["name", "prompt", "tools"])             # field order kept
        code, text = self.run_cli("apply", "-y")                                                  # and the result applies
        self.assertEqual(code, 0, text)
        written = json.load(open(os.path.join(self.agents, "a.json")))
        self.assertIn("@fake", written["tools"])
        self.assertTrue(written["includeMcpJson"])

    def test_add_all_with_tools_then_widen_and_repeat_is_a_no_op(self):
        self.agents_world("a", "b")
        self.assertEqual(self.run_cli("mcp", "add", "fake", "--all", "--tools", "alpha,beta", "-y")[0], 0)
        self.assertEqual(self.config_of("a")["mcp_json_servers"], {"fake": {"tools": ["alpha", "beta"]}})
        code, text = self.run_cli("mcp", "add", "fake", "--all", "--tools", "alpha,beta", "-y")
        self.assertIn("Nothing to change", text)
        self.assertEqual(self.run_cli("mcp", "add", "fake", "a", "--tools", "gamma", "-y")[0], 0)
        self.assertEqual(self.config_of("a")["mcp_json_servers"], {"fake": {"tools": ["alpha", "beta", "gamma"]}})
        self.assertEqual(self.config_of("b")["mcp_json_servers"], {"fake": {"tools": ["alpha", "beta"]}})

    def test_add_a_second_server_keeps_the_first_and_remove_takes_one_away(self):
        self.write_mcp({**json.load(open(self.mcp))["mcpServers"], "second": {"command": "x"}})
        self.agents_world("a", a={"mcp_json_servers": ["fake"]})
        self.assertEqual(self.run_cli("mcp", "add", "second", "a", "-y")[0], 0)
        self.assertEqual(self.config_of("a")["mcp_json_servers"], ["fake", "second"])
        self.assertEqual(self.run_cli("mcp", "remove", "fake", "a", "-y")[0], 0)
        self.assertEqual(self.config_of("a")["mcp_json_servers"], ["second"])
        self.assertEqual(self.run_cli("mcp", "remove", "second", "a", "-y")[0], 0)
        self.assertNotIn("mcp_json_servers", self.config_of("a"))
        code, text = self.run_cli("mcp", "remove", "second", "a", "-y")
        self.assertIn("Nothing to change", text)

    def test_add_errors_and_nothing_is_written(self):
        self.agents_world("a")
        for argv, message in ((("nope", "a", "-y"), "'nope' is not in"), (("off", "a", "-y"), "is disabled in"),
                              (("fake", "zzz", "-y"), "unknown crewmate(s): zzz"), (("fake", "-y"), "name the crewmates"),
                              (("fake", "a", "--tools", "bad name", "-y"), "--tools takes tool names")):
            code, text = self.run_cli("mcp", "add", *argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(message, text)
        self.assertNotIn("mcp_json_servers", self.config_of("a"))

    def test_add_asks_first_and_refuses_without_a_terminal(self):
        self.agents_world("a")
        code, text = self.run_cli("mcp", "add", "fake", "a")
        self.assertEqual(code, 1)
        self.assertIn("Use --yes", text)
        self.assertNotIn("mcp_json_servers", self.config_of("a"))

    def test_add_is_undone_when_it_would_make_the_config_invalid(self):
        self.agents_world("a", "b", a={"include_mcp_json": False})        # a refuses mcp_json_servers
        before = open(os.path.join(self.config, "a.agent.json")).read()
        code, text = self.run_cli("mcp", "add", "fake", "a", "b", "-y")
        self.assertEqual(code, 1)
        self.assertIn("so it was undone", text)
        self.assertIn("needs include_mcp_json to be true", text)
        self.assertEqual(open(os.path.join(self.config, "a.agent.json")).read(), before)
        self.assertNotIn("mcp_json_servers", self.config_of("b"))          # the other file was put back too

    def test_tools_lists_every_tool_across_pages_over_stdio(self):
        code, text = self.run_cli("mcp", "tools", "fake", "-y")
        self.assertEqual(code, 0, text)
        self.assertIn("fake: 3 tool(s)", text)
        for name in ("alpha", "beta", "gamma"):
            self.assertIn(name, text)
        self.assertIn("First tool with spaces", text)                       # whitespace in descriptions is collapsed
        self.assertIn('"tools": ["alpha"]', text)

    def test_tools_asks_before_starting_a_server_and_never_prints_environment_values(self):
        code, text = self.run_cli("mcp", "tools", "fake")                   # no terminal, no --yes
        self.assertEqual(code, 1)
        self.assertIn("Will start fake:", text)
        self.assertIn("Use --yes", text)
        self.assertNotIn("hunter2hunter2", text)
        with mock.patch.dict(os.environ, {"FAKE_MODE": "exit"}):
            code, text = self.run_cli("mcp", "tools", "fake", "-y")
        self.assertEqual(code, 1)
        self.assertIn("Cannot get the tool list", text)
        self.assertIn("boom: missing API key", text)
        self.assertNotIn("hunter2hunter2", text)                            # the server echoed its secret on stderr: redacted

    def test_tools_errors(self):
        code, text = self.run_cli("mcp", "tools", "nope", "-y")
        self.assertEqual((code, "'nope' is not in" in text), (1, True))
        with mock.patch.dict(os.environ, {"FAKE_MODE": "hang"}):
            code, text = self.run_cli("mcp", "tools", "fake", "-y", "--timeout", "2")
        self.assertEqual(code, 1)
        self.assertIn("did not answer in time", text)
        code, text = self.run_cli("mcp", "tools", "remote", "-y")           # ${TOKEN_FOR_TEST} is not set
        self.assertEqual(code, 1)
        self.assertIn("TOKEN_FOR_TEST", text)
        self.assertIn("not set in this terminal", text)
        code, text = self.run_cli("mcp", "tools", "off", "-y")
        self.assertEqual(code, 0)
        self.assertIn("is disabled in mcp.json", text)

    def test_tools_over_http_json_and_event_stream(self):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            sse = False
            seen = []

            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                Handler.seen.append((body.get("method"), self.headers.get("Authorization"), self.headers.get("Mcp-Session-Id")))
                if "id" not in body:
                    self.send_response(202)
                    self.end_headers()
                    return
                result = {"protocolVersion": "2025-03-26"} if body["method"] == "initialize" else {"tools": [{"name": "http_tool", "description": "over http"}]}
                payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
                self.send_response(200)
                self.send_header("Mcp-Session-Id", "sess-1")
                if Handler.sse:
                    data = f"event: message\ndata: {payload}\n\n".encode()
                    self.send_header("Content-Type", "text/event-stream")
                else:
                    data = payload.encode()
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.write_mcp({"remote": {"url": f"http://127.0.0.1:{httpd.server_address[1]}/mcp", "headers": {"Authorization": "Bearer ${TOKEN_FOR_TEST}"}}})
        with mock.patch.dict(os.environ, {"TOKEN_FOR_TEST": "tok-123456"}):
            for sse in (False, True):
                Handler.sse, Handler.seen = sse, []
                code, text = self.run_cli("mcp", "tools", "remote", "-y")
                self.assertEqual(code, 0, text)
                self.assertIn("http_tool", text)
                self.assertNotIn("tok-123456", text)
                self.assertEqual([m for m, _, _ in Handler.seen], ["initialize", "notifications/initialized", "tools/list"])
                self.assertTrue(all(a == "Bearer tok-123456" for _, a, _ in Handler.seen))
                self.assertEqual(Handler.seen[-1][2], "sess-1")             # the session id from initialize is sent back


class GuideTests(unittest.TestCase):
    def setUp(self):
        from crewctl import guide as guide_module
        self.g = guide_module
        self.env = Env(workspaces={"default": {"dir": "workspace"}, "ops": {"dir": "workspace-ops"}})
        self.addCleanup(self.env.close)
        for name in ("alpha", "beta"):
            self.env.agent(name)

    def make(self, answers, run=None):
        feed = iter(answers)
        self.out = []

        def fake_input(prompt=""):
            self.out.append(prompt)
            try:
                return next(feed)
            except StopIteration:
                raise EOFError
        self.ran = []
        run = run or (lambda argv: self.ran.append(argv) or 0)
        return self.g.Guide(cli.build_parser(), self.env.cfg, {"agents_dir": self.env.agents, "crew_home": self.env.crew_home},
                            color=False, input_fn=fake_input, print_fn=self.out.append, run_fn=run)

    def text(self):
        return "\n".join(self.out)

    def command(self, *path):
        return next(c for c in self.g.commands(cli.build_parser()) if c.path == path)

    # ---- the data is complete and cannot drift from the parser
    def test_every_command_has_an_entry_and_every_entry_is_a_command(self):
        paths = {c.path for c in self.g.commands(cli.build_parser())}
        self.assertEqual(paths, set(self.g.INFO))
        for path, (impact, effect, words) in self.g.INFO.items():
            self.assertIn(impact, self.g.IMPACT, path)
            self.assertGreater(len(effect), 20, path)
        self.assertEqual({p for p in paths if p[0] == "skills"}, {("skills", w) for w in ("new", "plan", "apply", "destroy")})
        self.assertNotIn(("guide",), paths)

    def test_options_that_change_things_have_an_explicit_effect_text(self):
        seen = set()
        for command in self.g.commands(cli.build_parser()):
            for arg in command.args:
                if not arg.positional:
                    seen.add(arg.dest)
                    self.assertTrue(arg.help or arg.dest in self.g.EFFECTS, (command.path, arg.dest))
        for risky in ("yes", "force", "run", "delete_records", "adopt", "all", "update", "break_lock", "target", "tools", "copy_from"):
            self.assertIn(risky, self.g.EFFECTS)
            self.assertIn(risky, seen)

    # ---- suggesting a command from a few words
    def test_words_find_the_right_command_first(self):
        cmds = self.g.commands(cli.build_parser())
        for query, expected in (("move crewmates to a workspace", ("workspace", "assign")), ("add an mcp server to agents", ("mcp", "add")),
                                ("preview what will change", ("plan",)), ("delete everything crewctl made", ("destroy",)),
                                ("restore the workspace after an overwrite", ("workspace", "restore")), ("list tools of a server", ("mcp", "tools")),
                                ("check my skill files", ("lint-skills",)), ("why is kirocrew core missing", ("crew-servers",))):
            top = [c.path for c in self.g.rank(query, cmds)[:3]]
            self.assertIn(expected, top, (query, top))
        self.assertEqual(self.g.rank("zzzz qqqq", cmds), [])

    # ---- the command line it builds
    def test_command_line_with_globals_flags_values_lists_and_quoting(self):
        guide = self.make([])
        guide.global_values["config_dir"] = "/my folder/crew"
        line = guide.line(self.command("mcp", "add"), {"server": "fetch", "crewmates": ["a", "b"], "tools": "x,y", "yes": True})
        self.assertEqual(line, "crewctl --config-dir '/my folder/crew' mcp add fetch a b --tools x,y --yes")
        self.assertTrue(guide.line(self.command("skills", "plan"), {"show": True}).endswith("skills plan --show"))
        self.assertTrue(guide.line(self.command("skills", "new"), {"name": "my-skill"}).endswith("skills new my-skill"))
        self.assertEqual(guide.line(self.command("apply"), {"target": ["a", "b"]}), "crewctl --config-dir '/my folder/crew' apply --target a --target b")

    # ---- the conversation
    def test_building_a_command_shows_the_command_and_the_effect_after_every_choice(self):
        guide = self.make(["move crewmates to a workspace", "1", "ops", "1 2", "1", "2", "p"])
        self.assertEqual(guide.run(), 0)
        text = self.text()
        self.assertIn("Best matches for 'move crewmates to a workspace'", text)
        self.assertIn("crewctl workspace assign ops alpha beta", text)                      # the command before any option
        self.assertIn("crewctl workspace assign ops alpha beta --all", text)                # after the first option
        self.assertIn("crewctl workspace assign ops alpha beta --all --yes", text)          # after the second
        self.assertIn("Last choice:", text)
        self.assertIn("added --all: Applies to every crewmate in the config", text)
        self.assertIn("added --yes: No confirmation question is asked", text)
        self.assertIn("Added so far:", text)
        self.assertIn("[CHANGES CREW]", text)
        self.assertEqual(self.ran, [])                                                      # `p` only prints

    def test_it_runs_only_after_yes_and_passes_the_exact_arguments(self):
        answers = ["plan", "1", "1", "alpha", "r", "yes"]
        guide = self.make(answers)
        self.assertEqual(guide.run(), 0)
        self.assertEqual(self.ran, [["plan", "--target", "alpha"]])
        guide = self.make(["plan", "1", "r", "no"])
        guide.run()
        self.assertEqual(self.ran, [])
        self.assertIn("Not run.", self.text())

    def test_values_can_be_picked_by_number_from_the_names_in_your_config(self):
        guide = self.make(["apply", "1", "1", "2", "p"])
        guide.run()
        self.assertIn("1) alpha", self.text())
        self.assertIn("crewctl apply --target beta", self.text())
        guide = self.make(["destroy", "1", "p"])
        guide.run()
        self.assertIn("--delete-records", self.text())

    def test_undo_toggle_explain_and_start_over(self):
        guide = self.make(["apply", "1", "2", "3", "u", "?3", "2", "x", "q"])       # apply; add --yes; add --force; undo; explain --force; remove --yes
        self.assertEqual(guide.run(), 0)
        text = self.text()
        self.assertIn("added --force", text)
        self.assertIn("undid the last choice", text)
        self.assertIn("explained --force (nothing changed)", text)
        self.assertIn("removed --yes", text)
        self.assertIn("What do you want to do?", text.split("Last choice:")[-1])            # `x` went back to choosing a command

    def test_required_arguments_are_asked_for_until_given(self):
        guide = self.make(["add mcp server", "1", "", "fetch", "", "p"])
        guide.run()
        self.assertIn("This one is required.", self.text())
        self.assertIn("crewctl mcp add fetch", self.text())

    def test_nothing_found_and_end_of_input_leave_cleanly(self):
        guide = self.make(["zzzz qqqq", "q"])
        self.assertEqual(guide.run(), 0)
        self.assertIn("I found nothing for that", self.text())
        with self.assertRaises(SystemExit) as stop:
            self.make([]).run()
        self.assertEqual(stop.exception.code, 0)

    def test_the_guide_command_is_registered_and_runs_through_the_cli(self):
        self.assertIn("guide", cli.build_parser()._subparsers._group_actions[0].choices)
        with mock.patch("builtins.input", side_effect=["q"]):
            code, text = self.env.run("guide")
        self.assertEqual(code, 0)
        self.assertIn("build a command step by step", text)


class CompletionTests(unittest.TestCase):
    def setUp(self):
        from crewctl import completion
        self.completion = completion
        self.parser = cli.build_parser()
        self.env = Env(workspaces={"default": {"dir": "workspace"}, "ops": {"dir": "workspace-ops"}})
        self.addCleanup(self.env.close)
        self.env.agent("alpha")
        self.env.agent("beta")

    def test_every_command_and_option_with_its_description_is_in_each_script(self):
        tree = self.completion.build_tree(self.parser)
        commands = [n for n in self.completion.walk(tree) if n.path]
        self.assertIn(("guide",), [n.path for n in commands])
        self.assertIn(("completion",), [n.path for n in commands])
        self.assertNotIn(("values",), [n.path for n in commands])
        for shell, script_fn in self.completion.SCRIPTS.items():
            script = script_fn(self.parser)
            for node in commands:
                self.assertIn(node.name, script, (shell, node.path))
                for arg in node.args:
                    for flag in arg.flags:
                        self.assertIn(flag.lstrip("-"), script, (shell, node.path, flag))
            if shell != "bash":                                    # bash cannot show descriptions; zsh and fish do
                self.assertIn("create one workspace" if shell == "fish" else "workspace", script)
                self.assertIn("limit to this crewmate", script)

    def test_quotes_are_balanced_on_every_line_of_the_zsh_and_fish_scripts(self):
        for shell in ("zsh", "fish"):
            for number, line in enumerate(self.completion.SCRIPTS[shell](self.parser).splitlines(), 1):
                cleaned = line.replace("'\\''", "").replace("\\'", "")
                self.assertEqual(cleaned.count("'") % 2, 0, (shell, number, line))

    def test_zsh_and_fish_scripts_pass_the_shells_own_syntax_check_when_installed(self):
        import subprocess
        for shell, flag in (("zsh", "-n"), ("fish", "--no-execute")):
            if not shutil.which(shell):
                continue
            script = self.completion.SCRIPTS[shell](self.parser)
            result = subprocess.run([shell, flag], input=script, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, (shell, result.stderr))

    def drive(self, *words):
        """Run the generated bash completion for real and return the suggestions for the last word."""
        import subprocess
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        script = os.path.join(folder, "c.bash")
        open(script, "w").write(self.completion.bash_script(self.parser))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        quoted = " ".join("'" + w + "'" for w in words)
        driver = f"""source {script}
crewctl() {{ (cd {root} && python3 -m crewctl "$@"); }}
COMP_WORDS=(crewctl {quoted}); COMP_CWORD=$(( ${{#COMP_WORDS[@]}} - 1 )); COMPREPLY=()
_crewctl
printf '%s\\n' "${{COMPREPLY[@]}}"
"""
        env = {**os.environ, "CREWCTL_DIR": self.env.cfg, "CREWCTL_AGENTS_DIR": self.env.agents, "CREWCTL_CREW_HOME": self.env.crew_home,
               "CREWCTL_MCP_JSON": os.path.join(folder, "mcp.json")}
        json.dump({"mcpServers": {"fetch": {"command": "x"}, "docs": {"command": "y"}}}, open(env["CREWCTL_MCP_JSON"], "w"))
        result = subprocess.run(["bash", "-c", driver], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split()

    def test_bash_completion_commands_subcommands_options_and_live_names(self):
        import subprocess
        self.assertEqual(subprocess.run(["bash", "-n"], input=self.completion.bash_script(self.parser), text=True).returncode, 0)
        top = self.drive("")
        for name in ("apply", "workspace", "mcp", "guide", "completion", "skills"):
            self.assertIn(name, top)
        self.assertNotIn("values", top)
        self.assertEqual(self.drive("wo"), ["workspace"])
        self.assertEqual(sorted(self.drive("workspace", "")), ["assign", "check", "create", "list", "restore"])
        self.assertEqual(sorted(self.drive("apply", "--t")), ["--target"])
        self.assertEqual(sorted(self.drive("apply", "--target", "")), ["alpha", "beta"])            # crewmates from the config
        self.assertEqual(sorted(self.drive("workspace", "assign", "")), ["default", "ops"])         # workspaces
        self.assertEqual(sorted(self.drive("mcp", "add", "")), ["docs", "fetch"])                   # servers in mcp.json
        self.assertEqual(sorted(self.drive("skills", "")), ["apply", "destroy", "new", "plan"])
        self.assertIn("--adopt", self.drive("skills", "apply", "--a"))
        self.assertIn("--yes", self.drive("destroy", "--y"))

    def test_values_command_prints_names_and_nothing_on_any_problem(self):
        code, text = self.env.run("values", "crewmates")
        self.assertEqual((code, text.split()), (0, ["alpha", "beta"]))
        self.assertEqual(self.env.run("values", "workspaces")[1].split(), ["default", "ops"])
        self.assertEqual(self.env.run("values", "teams")[1].split(), ["devteam", "example"])
        self.assertEqual(self.env.run("values", "skills")[1].split(), [])
        with self.assertRaises(SystemExit):
            self.env.run("values", "nonsense")

    def test_completion_command_prints_each_script(self):
        for shell in ("bash", "zsh", "fish"):
            code, text = self.env.run("completion", shell)
            self.assertEqual(code, 0)
            self.assertIn("crewctl", text)

    def test_every_command_line_the_guide_can_build_is_accepted_by_the_real_parser(self):
        from crewctl import guide
        g = guide.Guide(self.parser, "/nonexistent", {}, color=False, input_fn=lambda p: "", print_fn=lambda *_: None)
        for command in g.commands:
            values = {}
            for arg in command.args:
                if arg.positional and arg.dest not in command.preset:
                    values[arg.dest] = ["x"] if arg.kind == "multi" else (arg.choices[0] if arg.choices else "x")
                elif not arg.positional:
                    values[arg.dest] = True if arg.kind == "flag" else (["v"] if arg.kind == "multi" else (arg.choices[0] if arg.choices else "5"))
            try:
                self.parser.parse_args(g.argv(command, values))
            except SystemExit:
                self.fail(f"the guide builds a command line the parser rejects: {command.path} {g.argv(command, values)}")


class Packaging(unittest.TestCase):
    def test_pyproject_exposes_a_crewctl_command_that_exists(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(root, "pyproject.toml"), encoding="utf-8").read()
        self.assertIn('crewctl = "crewctl.cli:main_entry"', text)
        self.assertTrue(callable(cli.main_entry))
        self.assertIn('crewctl = ["models.json"]', text)
        self.assertTrue(os.path.exists(os.path.join(root, "crewctl", "models.json")))

    def test_main_entry_exits_with_the_command_status(self):
        with mock.patch.object(sys, "argv", ["crewctl", "--version"]), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.main_entry()
        self.assertEqual(caught.exception.code, 0)


class ReviewCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kit = os.path.join(self.tmp, "kit")
        self.project = os.path.join(self.tmp, "proj")
        os.makedirs(self.kit)
        os.makedirs(self.project)
        self.log = os.path.join(self.tmp, "args.txt")
        with open(os.path.join(self.kit, "install.sh"), "w") as handle:
            handle.write(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{self.log}"\nexit ${{FAKE_EXIT:-0}}\n')
        patcher = mock.patch.dict(os.environ, {"CREWCTL_REVIEW_KIT": self.kit})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--no-color", *argv])
        return code, buffer.getvalue()

    def test_no_command_prints_the_two_jobs(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("JOB 1", out)
        self.assertIn("JOB 2", out)
        self.assertEqual(self.run_cli("overview")[0], 0)

    def test_install_passes_the_options_to_the_script(self):
        code, _ = self.run_cli("review", "install", self.project, "--surface", "ide", "--reference-source", "/refs/x", "--force")
        self.assertEqual(code, 0)
        args = open(self.log).read().split("\n")
        self.assertEqual(args[:5], [os.path.realpath(self.project), "--surface", "ide", "--reference-source", "/refs/x"])
        self.assertIn("--force", args)

    def test_install_defaults_to_crew_and_returns_the_script_status(self):
        self.run_cli("review", "install", self.project)
        self.assertEqual(open(self.log).read().split("\n")[1:3], ["--surface", "crew"])
        with mock.patch.dict(os.environ, {"FAKE_EXIT": "2"}):
            self.assertEqual(self.run_cli("review", "install", self.project)[0], 2)

    def test_bad_project_and_missing_kit(self):
        code, out = self.run_cli("review", "install", os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 1)
        self.assertIn("is not a folder", out)
        with mock.patch.dict(os.environ, {"CREWCTL_REVIEW_KIT": os.path.join(self.tmp, "empty")}), \
                mock.patch("os.path.isfile", side_effect=lambda p: False if p.endswith("install.sh") else os.path.exists(p)):
            code, out = self.run_cli("review", "where")
        self.assertEqual(code, 1)
        self.assertIn("Cannot find the agent-review kit", out)

    def test_where_and_check(self):
        code, out = self.run_cli("review", "where")
        self.assertEqual((code, self.kit in out.replace("~", os.path.expanduser("~"))), (0, True))
        code, out = self.run_cli("review", "check", self.project)
        self.assertEqual(code, 1)
        self.assertIn("Install the kit first", out)
        tools = os.path.join(self.project, "tools", "agent_review")
        os.makedirs(tools)
        with open(os.path.join(tools, "check_citations.py"), "w") as handle:
            handle.write("import sys\nprint('checked', sys.argv[1:])\nsys.exit(3)\n")
        code, _ = self.run_cli("review", "check", self.project, "--", "--recent")
        self.assertEqual(code, 3)

    def test_real_kit_is_found_in_the_repository(self):
        with mock.patch.dict(os.environ, {"CREWCTL_REVIEW_KIT": ""}):
            code, out = self.run_cli("review", "where")
        self.assertEqual(code, 0, out)
        self.assertIn("aiops", out)


class InstallerScript(unittest.TestCase):
    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def run_cmd(self, argv, **kwargs):
        import subprocess
        return subprocess.run(argv, capture_output=True, text=True, timeout=60, **kwargs)

    def test_scripts_are_valid_and_the_installer_checks_its_arguments(self):
        for name in ("install.sh", os.path.join("bin", "crewctl")):
            path = os.path.join(self.ROOT, name)
            self.assertTrue(os.access(path, os.X_OK), name)
            self.assertEqual(self.run_cmd(["bash", "-n", path]).returncode, 0, name)
        result = self.run_cmd(["bash", os.path.join(self.ROOT, "install.sh"), "--bogus"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr)

    def test_install_links_the_launcher_and_the_link_runs_from_anywhere(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        env = {**os.environ, "HOME": tmp, "CREWCTL_BIN_DIR": os.path.join(tmp, "bin")}
        result = self.run_cmd(["bash", os.path.join(self.ROOT, "install.sh")], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        link = os.path.join(tmp, "bin", "crewctl")
        self.assertTrue(os.path.islink(link))
        run = self.run_cmd([link, "--version"], cwd="/", env=env)
        self.assertEqual((run.returncode, run.stdout.strip()), (0, "crewctl 0.1.0"))
        self.assertEqual(self.run_cmd([link, "review", "where"], cwd="/", env=env).returncode, 0)
        # a file that is not our link is never replaced
        os.remove(link)
        open(link, "w").write("mine")
        self.assertEqual(self.run_cmd(["bash", os.path.join(self.ROOT, "install.sh")], env=env).returncode, 2)
        self.assertEqual(open(link).read(), "mine")
        # uninstall removes only our own link
        self.assertEqual(self.run_cmd(["bash", os.path.join(self.ROOT, "install.sh"), "--uninstall"], env=env).returncode, 0)
        self.assertTrue(os.path.exists(link))
        os.remove(link)
        os.symlink(os.path.join(self.ROOT, "bin", "crewctl"), link)
        self.run_cmd(["bash", os.path.join(self.ROOT, "install.sh"), "--uninstall"], env=env)
        self.assertFalse(os.path.lexists(link))

    def test_launcher_picks_the_python_you_name_and_reports_a_bad_one(self):
        launcher = os.path.join(self.ROOT, "bin", "crewctl")
        env = {**os.environ, "CREWCTL_PYTHON": sys.executable}
        self.assertEqual(self.run_cmd([launcher, "--version"], cwd="/", env=env).stdout.strip(), "crewctl 0.1.0")
        bad = self.run_cmd([launcher, "--version"], env={**os.environ, "CREWCTL_PYTHON": "/nonexistent/python"})
        self.assertEqual(bad.returncode, 2)
        self.assertIn("CREWCTL_PYTHON", bad.stderr)

    def test_launcher_does_not_depend_on_pythonpath_and_explains_a_misplaced_copy(self):
        launcher = os.path.join(self.ROOT, "bin", "crewctl")
        env = {**os.environ, "PYTHONPATH": "/nonexistent", "PYTHONSAFEPATH": "1"}
        self.assertEqual(self.run_cmd([launcher, "review", "where"], cwd="/", env=env).returncode, 0)
        debug = self.run_cmd([launcher, "--version"], env={**os.environ, "CREWCTL_DEBUG": "1"})
        self.assertIn("repository=", debug.stderr)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "bin"))
        lonely = os.path.join(tmp, "bin", "crewctl")
        shutil.copy(launcher, lonely)
        result = self.run_cmd([lonely, "--version"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("it is not there", result.stderr)
        self.assertIn(tmp, result.stderr)

    def test_python_dash_m_from_the_repository_root_fails_as_the_docs_warn(self):
        result = self.run_cmd([sys.executable, "-m", "crewctl", "--version"], cwd=self.ROOT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No module named crewctl", result.stderr)



class ColoredHelp(unittest.TestCase):
    def run_cli(self, *argv, env=None):
        import subprocess
        base = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR")}
        base.update(env or {})
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run([sys.executable, "-m", "crewctl", *argv], capture_output=True, text=True, cwd=root, env=base, timeout=60)

    def test_no_color_when_piped_or_disabled(self):
        self.assertNotIn("\033", self.run_cli().stdout)                                   # piped, not a terminal
        self.assertNotIn("\033", self.run_cli("--no-color", env={"FORCE_COLOR": "1"}).stdout)
        self.assertNotIn("\033", self.run_cli(env={"FORCE_COLOR": "1", "NO_COLOR": "1"}).stdout)
        self.assertNotIn("\033", self.run_cli("apply", "--help", env={"NO_COLOR": "1"}).stdout)

    def test_overview_uses_four_kinds_of_color(self):
        out = self.run_cli(env={"FORCE_COLOR": "1"}).stdout
        self.assertIn("\033[1;36mcrewctl apply\033[0m", out)         # command
        self.assertIn("\033[32m--target\033[0m", out)                # option
        self.assertIn("\033[33mNAME\033[0m", out)                   # parameter
        self.assertIn("\033[33mnew|plan|apply|destroy\033[0m", out)  # choices
        self.assertIn("start a config folder", out)                  # description stays plain
        self.assertNotIn("\033[2mstart", out)

    def test_command_help_is_colored_the_same_way(self):
        out = self.run_cli("apply", "--help", env={"FORCE_COLOR": "1"}).stdout
        self.assertIn("\033[1;36mcrewctl apply\033[0m", out)
        self.assertIn("\033[32m--target\033[0m \033[33mNAME\033[0m", out)
        self.assertIn("limit to this crewmate (repeatable)", out)
        group = self.run_cli("review", "--help", env={"FORCE_COLOR": "1"}).stdout
        self.assertIn("\033[1;36minstall\033[0m", group)
        positional = self.run_cli("review", "install", "--help", env={"FORCE_COLOR": "1"}).stdout
        self.assertIn("\033[33mproject\033[0m", positional)

    def test_plain_text_is_the_same_with_and_without_color(self):
        import re
        strip = lambda t: re.sub(r"\033\[[0-9;]*m", "", t)
        for argv in ((), ("apply", "--help"), ("review", "install", "--help"), ("skills", "--help")):
            plain = self.run_cli(*argv).stdout
            colored = self.run_cli(*argv, env={"FORCE_COLOR": "1"}).stdout
            self.assertEqual(strip(colored), plain, argv)

    def test_overview_lists_exactly_the_commands_the_parser_has(self):
        from crewctl.helptext import JOBS
        parser = cli.build_parser()
        names = set()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                names = set(action.choices) - {"overview", "values"}
                review = set(action.choices["review"]._actions[-1].choices)
        listed = {entry[0].split()[1] for _, entries in JOBS for entry in entries}
        self.assertEqual(listed, names)
        listed_review = {entry[0].split()[2] for _, entries in JOBS for entry in entries if entry[0].startswith("crewctl review")}
        self.assertEqual(listed_review, review)


class InitTeams(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--no-color", *argv])
        return code, buffer.getvalue()

    def test_plain_init_says_there_are_no_crewmates_and_how_to_get_some(self):
        folder = os.path.join(self.tmp, "c")
        code, out = self.run_cli("-c", folder, "init")
        self.assertEqual(code, 0)
        self.assertIn("no crewmates yet", out)
        self.assertIn("--team devteam", out)
        self.assertFalse([f for f in os.listdir(folder) if f.endswith(".agent.json") and not f.startswith("_")])
        code, out = self.run_cli("-c", folder, "validate")
        self.assertIn("Copy _template.agent.json", out)

    def test_init_team_devteam_copies_agents_prompts_skills_and_scripts_but_not_state_or_docs(self):
        folder = os.path.join(self.tmp, "c")
        code, out = self.run_cli("-c", folder, "init", "--team", "devteam")
        self.assertEqual(code, 0, out)
        agents = sorted(f for f in os.listdir(folder) if f.endswith("-agent.agent.json"))
        self.assertEqual(len(agents), 10)
        self.assertTrue(os.path.exists(os.path.join(folder, "prompts", "orchestrator-agent.md")))
        self.assertTrue(os.path.exists(os.path.join(folder, "skills", "assets", "incident", "collect_evidence.py")))
        self.assertTrue(os.access(os.path.join(folder, "skills", "assets", "incident", "collect_evidence.py"), os.X_OK))
        for unwanted in (".crewctl", "README.md", "SETUP.md"):
            self.assertFalse(os.path.exists(os.path.join(folder, unwanted)), unwanted)
        self.assertIn("plan", json.load(open(os.path.join(folder, "crewctl.json"))))
        # the copy is a working team: the skills build from it
        with mock.patch.dict(os.environ, {"CREWCTL_SKILLS_DIR": os.path.join(self.tmp, "skills")}):
            code, out = self.run_cli("-c", folder, "skills", "apply", "-y")
        self.assertEqual(code, 0, out)

    def test_init_is_refused_before_writing_when_the_folder_has_agents(self):
        folder = os.path.join(self.tmp, "c")
        os.makedirs(folder)
        open(os.path.join(folder, "mine.agent.json"), "w").write("{}")
        code, out = self.run_cli("-c", folder, "init", "--team", "example")
        self.assertEqual(code, 1)
        self.assertIn("already has agent files", out)
        self.assertFalse(os.path.exists(os.path.join(folder, "crewctl.json")))

    def test_update_refreshes_a_copy_keeps_settings_and_saves_what_it_overwrites(self):
        folder = os.path.join(self.tmp, "c")
        self.run_cli("-c", folder, "init", "--team", "devteam")
        settings = os.path.join(folder, "crewctl.json")
        json.dump({"plan": "paid", "use_crew_servers": False}, open(settings, "w"))
        edited = os.path.join(folder, "prompts", "orchestrator-agent.md")
        original = open(edited).read()
        open(edited, "w").write("my own edit\n")
        os.remove(os.path.join(folder, "prompts", "_language.md"))
        mine = os.path.join(folder, "prompts", "my-notes.md")
        open(mine, "w").write("keep me\n")
        code, text = self.run_cli("-c", folder, "init", "--team", "devteam", "--update")            # no terminal, no --yes
        self.assertEqual(code, 1)
        self.assertIn("Use --yes", text)
        self.assertEqual(open(edited).read(), "my own edit\n")
        code, text = self.run_cli("-c", folder, "init", "--team", "devteam", "--update", "--yes")
        self.assertEqual(code, 0, text)
        self.assertIn("1 new, 1 changed", text)
        self.assertEqual(open(edited).read(), original)
        self.assertTrue(os.path.exists(os.path.join(folder, "prompts", "_language.md")))
        self.assertEqual(json.load(open(settings)), {"plan": "paid", "use_crew_servers": False})   # settings untouched
        self.assertEqual(open(mine).read(), "keep me\n")                                           # your own files untouched
        backups = [os.path.join(r, f) for r, _, fs in os.walk(os.path.join(folder, ".crewctl", "backups")) for f in fs]
        self.assertEqual([open(b).read() for b in backups if b.endswith("orchestrator-agent.md")], ["my own edit\n"])
        code, text = self.run_cli("-c", folder, "init", "--team", "devteam", "--update", "--yes")
        self.assertIn("Nothing to update", text)

    def test_update_keeps_the_workspace_you_chose_in_agent_files(self):
        folder = os.path.join(self.tmp, "c")
        self.run_cli("-c", folder, "init", "--team", "devteam")
        gen = os.path.join(folder, "go-agent.agent.json")
        other = os.path.join(folder, "rust-agent.agent.json")
        for path in (gen, other):
            data = json.load(open(path))
            data["workspace"] = "my-project"
            data["workspace_dir"] = "workspace-my-project"
            json.dump(data, open(path, "w"), indent=2)
        data = json.load(open(gen))
        data["description"] = "edited by hand"
        json.dump(data, open(gen, "w"), indent=2)
        code, text = self.run_cli("-c", folder, "init", "--team", "devteam", "--update", "--yes")
        self.assertEqual(code, 0, text)
        self.assertIn("Kept the workspace you chose in", text)
        updated = json.load(open(gen))
        self.assertEqual((updated["workspace"], updated["workspace_dir"]), ("my-project", "workspace-my-project"))
        self.assertNotEqual(updated["description"], "edited by hand")                # the team's text came back
        self.assertIn("1 changed", text)
        # a file where only the workspace differs from the team is not reported as changed
        self.assertNotIn("rust-agent.agent.json", text)
        self.assertEqual(json.load(open(other))["workspace"], "my-project")
        code, text = self.run_cli("-c", folder, "init", "--team", "devteam", "--update", "--yes")
        self.assertIn("Nothing to update", text)

    def test_update_needs_a_team_and_an_existing_copy(self):
        folder = os.path.join(self.tmp, "c")
        code, text = self.run_cli("-c", folder, "init", "--update")
        self.assertEqual(code, 1)
        self.assertIn("--update needs --team", text)
        code, text = self.run_cli("-c", folder, "init", "--team", "devteam", "--update", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("has no crewctl.json", text)

    def test_force_keeps_other_agent_files_and_says_so(self):
        folder = os.path.join(self.tmp, "c")
        self.run_cli("-c", folder, "init", "--team", "devteam")
        code, out = self.run_cli("-c", folder, "init", "--force", "--team", "example")
        self.assertEqual(code, 0, out)
        self.assertIn("Kept 10 agent file(s)", out)
        self.assertTrue(os.path.exists(os.path.join(folder, "conductor.agent.json")))


class WorkspaceCommands(unittest.TestCase):
    def make(self, **kwargs):
        kwargs.setdefault("workspaces", {"default": {"dir": "workspace"}, "ops": {"dir": "workspace-ops"}})
        env = Env(**kwargs)
        self.addCleanup(env.close)
        self.marker = os.path.join(env.root, "ran")
        env.settings_file(plan="free", kirocrew_bin=Registration.fake_kirocrew(env, self.marker))
        return env

    def ran(self):
        if not os.path.exists(self.marker):
            return []
        with open(self.marker, encoding="utf-8") as handle:
            return handle.read().splitlines()

    class Terminal:
        def isatty(self):
            return True

    def interactive(self, env, answers, *argv):
        feed = iter(answers)
        asked = []

        def fake_input(prompt=""):
            asked.append(prompt)
            return next(feed)
        with mock.patch("sys.stdin", self.Terminal()), mock.patch("builtins.input", fake_input):
            code, text = env.run(*argv)
        return code, text, asked

    def test_agent_using_an_unknown_workspace_blocks_plan_apply_register_validate_but_not_destroy_or_import(self):
        env = self.make()
        env.agent("a", workspace="logs")
        for command in ("validate", "plan", "apply", "register", "status"):
            code, text = env.run(command, *(["--yes"] if command == "apply" else []))
            self.assertEqual(code, 1, (command, text))
            self.assertIn("workspace 'logs' does not exist in Crew (it has: default, ops)", text)
        self.assertNotEqual(env.run("destroy", "--yes")[1].count("workspace 'logs' does not exist"), 1)

    def test_known_workspace_and_default_are_fine_and_invalid_names_are_rejected(self):
        env = self.make()
        env.agent("a", workspace="ops")
        env.agent("b", workspace="default")
        self.assertEqual(env.run("validate")[0], 0)
        env.agent("c", workspace="bad name!")
        code, text = env.run("validate")
        self.assertEqual(code, 1)
        self.assertIn("not a valid Crew workspace name", text)

    def test_unreadable_crew_config_only_warns(self):
        env = self.make(with_crew_config=False)
        os.rmdir(env.crew_home)
        env.agent("a", workspace="logs")
        code, text = env.run("validate")
        self.assertEqual(code, 0, text)
        self.assertIn("cannot check that the workspaces exist", text)

    def test_fresh_crew_without_config_json_has_only_the_default_workspace(self):
        env = self.make(with_crew_config=False)
        env.agent("a", workspace="logs")
        code, text = env.run("validate")
        self.assertEqual(code, 1)
        self.assertIn("workspace 'logs' does not exist in Crew (it has: none)", text)

    def test_list_shows_workspaces_and_who_uses_them(self):
        env = self.make()
        env.agent("a", workspace="ops")
        code, text = env.run("workspace", "list")
        self.assertEqual(code, 0)
        self.assertRegex(text, r"ops\s+workspace-ops\s+a")
        self.assertRegex(text, r"default \*\s+workspace")

    def test_check_with_nothing_missing(self):
        env = self.make()
        env.agent("a", workspace="ops")
        code, text = env.run("workspace", "check")
        self.assertEqual(code, 0)
        self.assertIn("every workspace the crewmates use exists", text)

    def test_check_without_a_terminal_prints_the_command_and_creates_nothing(self):
        env = self.make()
        env.agent("a", workspace="logs", workspace_dir="my-logs")
        code, text = env.run("workspace", "check")
        self.assertEqual(code, 1)
        self.assertIn("workspace create --name logs --dir my-logs", text)
        self.assertEqual(self.ran(), [])

    def test_check_yes_creates_with_defaults_and_optional_copy_from(self):
        env = self.make()
        env.agent("a", workspace="logs")
        env.agent("b", workspace="metrics", workspace_dir="m-folder")
        code, text = env.run("workspace", "check", "--yes", "--copy-from", "ops")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.ran(), ["workspace create --name logs --dir workspace-logs --copy-from ops",
                                      "workspace create --name metrics --dir m-folder --copy-from ops"])
        code, text = env.run("workspace", "check", "--yes", "--copy-from", "nope")      # the fake kirocrew does not write Crew's config
        self.assertEqual(code, 1)
        self.assertIn("no workspace named 'nope'", text)
        self.assertEqual(len(self.ran()), 2)

    def test_check_asks_for_name_folder_and_copy_source_and_confirms(self):
        env = self.make()
        env.agent("a", workspace="logs")
        answers = ["", "bad/../dir", "/abs", "ws-logs", "nope", "ops", "yes"]
        code, text, asked = self.interactive(env, answers, "workspace", "check")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.ran(), ["workspace create --name logs --dir ws-logs --copy-from ops"])
        joined = "\n".join(asked)
        for question in ("Workspace name", "Folder name under", "Copy the files of an existing workspace", "Create it?"):
            self.assertIn(question, joined)
        self.assertIn("give a plain folder name inside Crew's data folder", text)
        self.assertIn("there is no workspace named 'nope'", text)

    def test_check_refuses_a_folder_already_used_and_a_name_that_exists(self):
        env = self.make()
        env.agent("a", workspace="logs")
        code, text, _ = self.interactive(env, ["ops", "logs", "workspace-ops", "ws-logs", "", "yes"], "workspace", "check")
        self.assertEqual(code, 0, text)
        self.assertIn("a workspace named 'ops' already exists", text)
        self.assertIn("already used by workspace 'ops'", text)
        self.assertEqual(self.ran(), ["workspace create --name logs --dir ws-logs"])

    def test_renaming_in_the_questions_warns_that_the_config_still_says_the_old_name(self):
        env = self.make()
        env.agent("a", workspace="logs")
        code, text, _ = self.interactive(env, ["audit", "", "", "yes"], "workspace", "check")
        self.assertEqual(code, 0, text)
        self.assertIn("config still says 'logs'", text)
        self.assertEqual(self.ran(), ["workspace create --name audit --dir workspace-audit"])

    def test_answering_no_or_quitting_creates_nothing(self):
        env = self.make()
        env.agent("a", workspace="logs")
        code, text, _ = self.interactive(env, ["", "", "", "no"], "workspace", "check")
        self.assertEqual(code, 1)
        self.assertEqual(self.ran(), [])
        code, text, _ = self.interactive(env, ["q"], "workspace", "check")
        self.assertEqual(code, 1)
        self.assertIn("Cancelled", text)
        self.assertEqual(self.ran(), [])

    def test_create_validates_and_runs_kirocrew(self):
        env = self.make()
        for argv, message in ((("logs", "--dir", "../x"), "plain folder name"), (("bad name",), "letters, digits"),
                              (("ops",), "already exists"), (("logs", "--copy-from", "zzz"), "no workspace named 'zzz'"),
                              (("logs", "--dir", "workspace-ops"), "already used by workspace 'ops'")):
            code, text = env.run("workspace", "create", *argv, "-y")
            self.assertEqual(code, 1, argv)
            self.assertIn(message, text)
        self.assertEqual(self.ran(), [])
        code, text = env.run("workspace", "create", "logs", "--dir", "ws/logs", "--copy-from", "ops", "-y")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.ran(), ["workspace create --name logs --dir ws/logs --copy-from ops"])
        code, text = env.run("workspace", "create", "metrics")          # no terminal, no --yes
        self.assertEqual(code, 1)
        self.assertEqual(len(self.ran()), 1)

    def fake_run_commands(self, env, calls):
        def run(binary, commands, log):
            for argv in commands:
                calls.append(" ".join(argv))
                if argv[:2] == ["workspace", "create"]:
                    path = os.path.join(env.crew_home, "config.json")
                    data = json.load(open(path))
                    data["workspaces"][argv[argv.index("--name") + 1]] = {"dir": argv[argv.index("--dir") + 1]}
                    json.dump(data, open(path, "w"))
            return len(commands)
        return run

    def test_register_offers_to_create_the_missing_workspace_and_carries_on(self):
        env = self.make()
        env.agent("a", workspace="logs")
        calls = []
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text, asked = self.interactive(env, ["yes", "", "", "", "yes"], "register")
        self.assertEqual(code, 0, text)
        self.assertIn("Create the missing workspace(s) now?", asked[0])
        self.assertEqual(calls, ["workspace create --name logs --dir workspace-logs"])
        self.assertIn("agent create --name a --kiro-agent a --workspace logs", text)

    def test_declining_the_offer_keeps_the_error(self):
        env = self.make()
        env.agent("a", workspace="logs")
        code, text, asked = self.interactive(env, ["no"], "apply")
        self.assertEqual(code, 1)
        self.assertIn("workspace 'logs' does not exist in Crew", text)
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.ran(), [])

    def test_no_offer_without_a_terminal_or_when_something_else_is_wrong(self):
        env = self.make()
        env.agent("a", workspace="logs")
        env.agent("b", prompt="")                       # another error: the offer must not appear
        code, text, asked = self.interactive(env, [], "plan")
        self.assertEqual((code, asked), (1, []))
        code, text = env.run("plan")                    # not a terminal
        self.assertEqual(code, 1)

    def test_register_uses_the_workspace_in_create_and_update_commands(self):
        env = self.make()
        env.agent("a", workspace="ops")
        action = engine.build_plan(env.ctx()).registrations[0]
        self.assertEqual(action.commands[0], ["agent", "create", "--name", "a", "--kiro-agent", "a", "--workspace", "ops"])
        env2 = self.make(crew_agents={"default": {"kiro_agent": "kirocrew"},
                                      "b": {"kiro_agent": "b", "workspace": "default", "description": "d", "triggers": "t"}})
        env2.agent("b", workspace="ops")
        action = next(r for r in engine.build_plan(env2.ctx()).registrations if r.name == "b")
        self.assertEqual(action.commands, [["agent", "update", "b", "--workspace", "ops"]])

    def assign_world(self, names=("a", "b", "c")):
        records = {"default": {"kiro_agent": "kirocrew"}}
        records["a"] = {"kiro_agent": "a", "workspace": "default", "description": "d", "triggers": "t"}
        records["b"] = {"kiro_agent": "b", "workspace": "default", "description": "d", "triggers": "t"}
        env = self.make(crew_agents=records)
        for name in names:
            env.agent(name)            # "c" is in the config but not registered in Crew
        return env

    def workspace_of(self, env, name):
        return json.load(open(os.path.join(env.cfg, f"{name}.agent.json"))).get("workspace")

    def test_assign_updates_the_config_file_and_the_crew_record(self):
        env = self.assign_world()
        path = os.path.join(env.cfg, "a.agent.json")
        before = json.load(open(path))
        calls = []
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text = env.run("workspace", "assign", "ops", "a", "--yes")
        self.assertEqual(code, 0, text)
        after = json.load(open(path))
        self.assertEqual(after["workspace"], "ops")
        self.assertEqual({k: v for k, v in after.items() if k != "workspace"}, {k: v for k, v in before.items() if k != "workspace"})
        self.assertEqual(calls, ["agent update a --workspace ops"])
        backups = os.path.join(env.cfg, ".crewctl", "backups")
        self.assertTrue(any(f == "a.agent.json" for _, _, files in os.walk(backups) for f in files))
        self.assertIn("1 crewmate(s) now work in workspace 'ops'", text)

    def test_assign_several_crewmates_at_once(self):
        env = self.assign_world()
        calls = []
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text = env.run("workspace", "assign", "ops", "a", "b", "c", "--yes")
        self.assertEqual(code, 0, text)
        self.assertEqual([self.workspace_of(env, n) for n in "abc"], ["ops", "ops", "ops"])
        self.assertEqual(calls, ["agent update a --workspace ops", "agent update b --workspace ops"])    # c is not in Crew yet
        self.assertIn("Not in Crew yet", text)
        self.assertIn("c", text.split("Not in Crew yet")[1])
        self.assertIn("2 crewmate(s) now work in workspace 'ops'", text)

    def test_assign_all_and_skipping_those_already_there(self):
        env = self.assign_world()
        env.agent("a", workspace="ops")
        calls = []
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text = env.run("workspace", "assign", "ops", "--all", "-y")
        self.assertEqual(code, 0, text)
        self.assertIn("a                            already ops", text)
        self.assertEqual([self.workspace_of(env, n) for n in "abc"], ["ops", "ops", "ops"])
        self.assertIn("agent update b --workspace ops", calls)

    def test_assign_to_a_missing_workspace_creates_it_once_with_yes_and_refuses_without(self):
        env = self.assign_world()
        code, text = env.run("workspace", "assign", "logs", "a", "b")                  # no terminal, no --yes
        self.assertEqual(code, 1)
        self.assertIn("Create it first", text)
        self.assertEqual(self.workspace_of(env, "a"), None)
        calls = []
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text = env.run("workspace", "assign", "logs", "a", "b", "-y")
        self.assertEqual(code, 0, text)
        self.assertEqual(calls, ["workspace create --name logs --dir workspace-logs",
                                 "agent update a --workspace logs", "agent update b --workspace logs"])

    def test_assign_asks_which_crewmates_when_none_are_given(self):
        env = self.assign_world()
        calls = []
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text, asked = self.interactive(env, ["", "a, b", "yes", "yes"], "workspace", "assign", "ops")
        self.assertEqual(code, 0, text)
        self.assertIn("Which crewmates?", asked[0])
        self.assertIn("give at least one name", text)
        self.assertEqual(calls, ["agent update a --workspace ops", "agent update b --workspace ops"])
        calls.clear()
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text, _ = self.interactive(env, ["all", "yes", "yes"], "workspace", "assign", "default")
        self.assertEqual(code, 0, text)
        # c never named a workspace (it inherits "default"), so there is nothing to edit in its file
        self.assertEqual([self.workspace_of(env, n) for n in "abc"], ["default", "default", None])

    def test_assign_declined_changes_nothing(self):
        env = self.assign_world()
        calls = []
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text, _ = self.interactive(env, ["no"], "workspace", "assign", "ops", "a", "b")
        self.assertEqual(code, 1)
        self.assertEqual([self.workspace_of(env, n) for n in "ab"], [None, None])
        self.assertEqual(calls, [])

    def test_assign_errors(self):
        env = self.assign_world()
        for argv, message in ((("ops", "zzz", "-y"), "unknown crewmate(s): zzz"), (("ops", "a", "zzz", "-y"), "unknown crewmate(s): zzz"),
                              (("bad name!", "a", "-y"), "not a valid workspace name"), (("ops",), "name the crewmates")):
            code, text = env.run("workspace", "assign", *argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(message, text)
        self.assertEqual([self.workspace_of(env, n) for n in "ab"], [None, None])      # nothing was edited on an error

    def test_assign_same_workspace_is_a_no_op(self):
        env = self.make(crew_agents={"default": {"kiro_agent": "kirocrew"},
                                     "a": {"kiro_agent": "a", "workspace": "ops", "description": "d", "triggers": "t"}})
        env.agent("a", workspace="ops")
        code, text = env.run("workspace", "assign", "ops", "a", "--yes")
        self.assertEqual(code, 0, text)
        self.assertIn("already ops", text)
        self.assertIn("already match", text)

    def test_register_warns_when_it_would_move_a_crewmate_to_another_workspace(self):
        env = self.make(crew_agents={"default": {"kiro_agent": "kirocrew"},
                                     "a": {"kiro_agent": "a", "workspace": "ops", "description": "d", "triggers": "t"}})
        env.agent("a")                                             # the config says default, Crew has ops
        code, text = env.run("register")
        self.assertIn("MOVES this crewmate from workspace 'ops' to 'default'", text)
        self.assertIn("crewctl workspace restore", text)

    def test_register_updates_every_crewmate_whose_record_differs_in_one_run(self):
        env = self.assign_world()
        for name in "abc":
            env.agent(name, workspace="ops", description="new")
        calls = []
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text = env.run("register", "--run", "--yes")
        self.assertEqual(code, 0, text)
        self.assertIn("agent update a --workspace ops", calls)
        self.assertIn("agent update b --workspace ops", calls)
        self.assertIn("agent create --name c --kiro-agent c --workspace ops", calls)
        self.assertIn('config set agents.a.description "new"', calls)

    def overwritten_world(self):
        """Config files that were overwritten by an update that dropped the workspace lines; the backups still have them."""
        records = {"default": {"kiro_agent": "kirocrew"},
                   "a": {"kiro_agent": "a", "workspace": "default", "description": "d", "triggers": "t"},
                   "b": {"kiro_agent": "b", "workspace": "default", "description": "d", "triggers": "t"}}
        env = self.make(crew_agents=records, workspaces={"default": {"dir": "workspace"}, "ops": {"dir": "workspace-ops"},
                                                         "dev": {"dir": "workspace-dev"}})
        env.agent("a")
        env.agent("b")
        env.agent("c")
        return env

    def backup(self, env, stamp, name, workspace, sub="team-update"):
        data = json.load(open(os.path.join(env.cfg, f"{name}.agent.json")))
        data.pop("workspace", None)
        if workspace is not None:
            data["workspace"] = workspace
        folder = os.path.join(env.cfg, ".crewctl", "backups", stamp, sub)
        os.makedirs(folder, exist_ok=True)
        json.dump(data, open(os.path.join(folder, f"{name}.agent.json"), "w"))

    def test_restore_puts_crewmates_back_on_the_workspace_a_backup_names(self):
        env = self.overwritten_world()
        self.backup(env, "20260101-000000", "a", "ops")
        self.backup(env, "20260101-000000", "b", "dev")
        calls = []
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text = env.run("workspace", "restore", "--yes")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.workspace_of(env, "a"), "ops")
        self.assertEqual(self.workspace_of(env, "b"), "dev")
        self.assertIsNone(self.workspace_of(env, "c"))                               # no backup: untouched
        self.assertEqual(sorted(calls), ["agent update a --workspace ops", "agent update b --workspace dev"])
        self.assertIn("a                            default -> ops", text)

    def test_restore_uses_the_newest_backup_and_respects_an_explicit_default(self):
        env = self.overwritten_world()
        self.backup(env, "20250101-000000", "a", "dev")                              # old
        self.backup(env, "20260101-000000", "a", "ops", sub="")                      # newer: ops wins
        self.backup(env, "20250101-000000", "b", "dev")
        self.backup(env, "20260101-000000", "b", "default")                          # newer says default on purpose
        calls = []
        with mock.patch("crewctl.engine.run_commands", self.fake_run_commands(env, calls)):
            code, text = env.run("workspace", "restore", "--yes")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.workspace_of(env, "a"), "ops")
        self.assertIsNone(self.workspace_of(env, "b"))
        self.assertEqual(calls, ["agent update a --workspace ops"])

    def test_restore_ignores_generated_agent_files_and_says_when_there_is_nothing(self):
        env = self.overwritten_world()
        code, text = env.run("workspace", "restore", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to restore", text)
        folder = os.path.join(env.cfg, ".crewctl", "backups", "20260101-000000")
        os.makedirs(folder)
        json.dump({"name": "a", "prompt": "generated kiro file"}, open(os.path.join(folder, "a.json"), "w"))      # not *.agent.json
        code, text = env.run("workspace", "restore", "--yes")
        self.assertIn("Nothing to restore", text)

    def test_restore_asks_first_and_refuses_without_a_terminal(self):
        env = self.overwritten_world()
        self.backup(env, "20260101-000000", "a", "ops")
        code, text = env.run("workspace", "restore")
        self.assertEqual(code, 1)
        self.assertIn("Use --yes", text)
        self.assertIsNone(self.workspace_of(env, "a"))
        code, text, _ = self.interactive(env, ["no"], "workspace", "restore")
        self.assertEqual(code, 1)
        self.assertIsNone(self.workspace_of(env, "a"))

    def test_a_failing_kirocrew_is_reported_and_check_exits_1(self):
        env = self.make()
        env.settings_file(plan="free", kirocrew_bin=Registration.fake_kirocrew(env, self.marker, exit_code=3))
        env.agent("a", workspace="logs")
        code, text = env.run("workspace", "check", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("failed (exit 3)", text)
        self.assertIn("still missing", text)

if __name__ == "__main__":
    unittest.main()
