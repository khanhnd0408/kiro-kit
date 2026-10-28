"""Tests for `crewctl map`: the page is written, carries the real command tree, and builds the same command lines as
`crewctl guide`. The last check runs the page's own JavaScript with Node; it is skipped when Node is not installed
(set NODE=/path/to/node to point at one). The interactive checks (clicking, Esc, undo) need a browser: see
tests/map_browser/README.md.

Run:  python3 -m unittest discover -s tests -v     (from the crewctl folder)
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from crewctl import cli, cmdmap, guide  # noqa: E402


def page_data(page):
    return json.loads(re.search(r"const DATA = (\{.*?\});\n", page, re.S).group(1))


class MapCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = cli.main(["--no-color", "map"] + list(argv))
        return code, buffer.getvalue()

    def test_writes_one_self_contained_page(self):
        target = os.path.join(self.tmp.name, "my map.html")
        code, out = self.run_cli("-o", target)
        self.assertEqual(code, 0, out)
        self.assertIn("Wrote", out)
        page = open(target, encoding="utf-8").read()
        for forbidden in ("http://", "https://", "<link", "<img", " src=", "@import"):
            self.assertNotIn(forbidden, page)
        self.assertEqual(page.count("<script>"), 1)
        self.assertEqual(page.count("</script>"), 1)
        code, _ = self.run_cli("-o", self.tmp.name)                       # a folder gets the default name
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "crewctl-map.html")))

    def test_the_page_carries_every_command_and_option_of_the_parser(self):
        parser = cli.build_parser()
        data = cmdmap.build_data(parser)
        commands = guide.commands(parser)
        self.assertEqual([c["path"] for c in data["commands"]], [list(c.path) for c in commands])
        for entry, command in zip(data["commands"], commands):
            self.assertEqual([a["dest"] for a in entry["args"]], [a.dest for a in command.args], command.path)
            self.assertEqual(entry["impact"], guide.INFO[command.path][0])
        self.assertEqual([a["dest"] for a in data["globals"]], [a.dest for a in guide.global_args(parser)])
        self.assertNotIn(["map"], [c["path"] for c in data["commands"]])     # crewctl's own helpers are not on the map
        target = os.path.join(self.tmp.name, "m.html")
        self.run_cli("-o", target)
        self.assertEqual(page_data(open(target, encoding="utf-8").read()), json.loads(json.dumps(data)))

    def test_data_can_never_close_the_script_tag(self):
        data = cmdmap.build_data(cli.build_parser())
        data["commands"][0]["effect"] = "</script><script>alert(1)</script>"
        page = cmdmap.render(data)
        self.assertEqual(page.count("</script>"), 1)
        self.assertEqual(page_data(page)["commands"][0]["effect"], "</script><script>alert(1)</script>")


def find_node():
    return os.environ.get("NODE") or shutil.which("node")


@unittest.skipUnless(find_node(), "Node is not installed (set NODE=/path/to/node to run this check)")
class SameLinesAsTheGuide(unittest.TestCase):
    """The page's JavaScript and `crewctl guide` build the same line for every command, every option set."""

    def test_every_command(self):
        parser = cli.build_parser()
        tool = guide.Guide(parser, tempfile.mkdtemp(), {}, color=False, input_fn=lambda *_: "", print_fn=lambda *_: None,
                           run_fn=lambda argv: 0)
        tool.global_values = {"config_dir": "/my folder/crew", "crew_home": "/srv/crew's home"}
        cases = []
        for command in guide.commands(parser):
            py, js = {}, {}
            for index, arg in enumerate(command.args):
                if arg.kind == "flag":
                    py[arg.dest] = js[arg.dest] = True
                elif arg.choices:
                    py[arg.dest] = js[arg.dest] = str(arg.choices[-1])
                elif arg.kind == "multi":
                    py[arg.dest] = ["alpha", "beta-2"]
                    js[arg.dest] = "alpha, beta-2"
                else:
                    py[arg.dest] = js[arg.dest] = f"v {index} it's" if index % 2 else f"v{index}"
            cases.append({"path": list(command.path), "values": js, "expected": tool.line(command, py)})
        page = cmdmap.render(cmdmap.build_data(parser))
        core = page[page.index("// CORE-START"):page.index("// CORE-END")]
        data = re.search(r"const DATA = (\{.*?\});\n", page, re.S).group(1)
        script = core + "\nconst DATA = " + data + ";\nconst cases = " + json.dumps(cases) + ";\n" + r"""
const globals = {config_dir: "/my folder/crew", crew_home: "/srv/crew's home"};
const out = cases.map(c => commandLine(buildTokens(DATA.commands.find(x => x.path.join(" ") === c.path.join(" ")), c.values, DATA.globals, globals)));
const flag = commandLine(buildTokens(DATA.commands.find(x => x.path.join(" ") === "validate"), {}, DATA.globals, {verbose: true}));
console.log(JSON.stringify({out, flag}));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(script)
        self.addCleanup(os.unlink, handle.name)
        result = json.loads(subprocess.run([find_node(), handle.name], capture_output=True, text=True, check=True, timeout=60).stdout)
        for case, line in zip(cases, result["out"]):
            self.assertEqual(line, case["expected"], case["path"])
        self.assertEqual(result["flag"], "crewctl --verbose validate")      # a global switch takes no value


if __name__ == "__main__":
    unittest.main()
