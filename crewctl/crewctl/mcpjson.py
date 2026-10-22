"""Read the user's MCP server list (default ~/.kiro/settings/mcp.json) and talk to one server to list its tools.

crewctl never copies a server's definition (its command, URL or environment) into an agent file. An agent that
uses a server from mcp.json only names it, and Kiro loads the definition from mcp.json when the agent starts.
`crewctl mcp tools` starts or calls the server once, the way Kiro would, so you can see the tool names to allow.
"""

import json
import os
import re
import selectors
import subprocess
import time
import urllib.error
import urllib.request

PROTOCOL = "2025-03-26"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
TOOL_RE = re.compile(r"^[A-Za-z0-9_.-]+\Z")
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
MAX_REPLY = 2_000_000


class McpError(RuntimeError):
    pass


def default_path():
    return os.environ.get("CREWCTL_MCP_JSON") or os.path.join(os.path.expanduser("~"), ".kiro", "settings", "mcp.json")


def load(path):
    """Return ({name: definition}, error text). The error is empty when the file was read."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}, f"no MCP file at {path}"
    except (OSError, ValueError) as error:
        return {}, f"cannot read {path}: {error}"
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}, f"{path} has no \"mcpServers\" object"
    return {k: v for k, v in servers.items() if isinstance(v, dict)}, ""


def kind(definition):
    return "url" if definition.get("url") else "command"


def expand(value, label):
    """Replace ${VAR} with the environment's value. A missing variable is an error, never an empty string."""
    def one(match):
        if match.group(1) not in os.environ:
            raise McpError(f"{label} uses ${{{match.group(1)}}} but that variable is not set in this terminal")
        return os.environ[match.group(1)]
    return VAR_RE.sub(one, value) if isinstance(value, str) else value


def command_line(definition):
    return " ".join([definition.get("command", "")] + [str(a) for a in definition.get("args", [])]).strip()


def redact(text, definition):
    """Remove the server's environment and header values from text before it is shown."""
    secrets = [str(v) for v in list((definition.get("env") or {}).values()) + list((definition.get("headers") or {}).values()) if v]
    for secret in sorted(set(secrets), key=len, reverse=True):
        if len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    return text


def _request(method, ident, params=None):
    message = {"jsonrpc": "2.0", "method": method}
    if ident is not None:
        message["id"] = ident
    if params is not None:
        message["params"] = params
    return message


INIT = _request("initialize", 1, {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "crewctl", "version": "0"}})
INITIALIZED = _request("notifications/initialized", None)


def _collect(tools_reply):
    result = tools_reply.get("result") if isinstance(tools_reply, dict) else None
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        raise McpError(f"the server did not answer tools/list with a tools list: {json.dumps(tools_reply)[:200]}")
    return [(str(t.get("name", "")), " ".join(str(t.get("description", "")).split())) for t in result["tools"] if isinstance(t, dict)]


class _Stdio:
    def __init__(self, definition, timeout):
        env = dict(os.environ)
        for key, value in (definition.get("env") or {}).items():
            env[str(key)] = expand(str(value), f"env.{key}")
        argv = [expand(definition["command"], "command")] + [expand(str(a), "args") for a in definition.get("args", [])]
        self.deadline = time.monotonic() + timeout
        try:
            self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        except OSError as error:
            raise McpError(f"cannot start the server: {error}")
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b""

    def send(self, message):
        try:
            self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise McpError("the server closed its input" + self.stderr_tail())

    def stderr_tail(self):
        try:
            self.process.kill()
            tail = self.process.stderr.read(600).decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            tail = ""
        return f": {tail}" if tail else ""

    def read_reply(self, ident):
        while True:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    message = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue                                   # log lines on stdout are not JSON-RPC
                if isinstance(message, dict) and message.get("id") == ident and ("result" in message or "error" in message):
                    if "error" in message:
                        raise McpError(f"the server answered with an error: {json.dumps(message['error'])[:200]}")
                    return message
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise McpError("the server did not answer in time" + self.stderr_tail())
            if not self.selector.select(min(remaining, 0.5)):
                if self.process.poll() is not None:
                    raise McpError(f"the server exited (code {self.process.returncode})" + self.stderr_tail())
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise McpError("the server closed its output" + self.stderr_tail())
            self.buffer += chunk
            if len(self.buffer) > MAX_REPLY:
                raise McpError("the server sent too much data")

    def close(self):
        for stream in (self.process.stdin,):
            try:
                stream.close()
            except OSError:
                pass
        try:
            self.process.terminate()
            self.process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()


def tools_over_stdio(definition, timeout=20):
    client = _Stdio(definition, timeout)
    try:
        client.send(INIT)
        client.read_reply(1)
        client.send(INITIALIZED)
        tools, cursor, ident = [], None, 2
        while True:
            client.send(_request("tools/list", ident, {"cursor": cursor} if cursor else {}))
            reply = client.read_reply(ident)
            tools += _collect(reply)
            cursor = reply["result"].get("nextCursor")
            ident += 1
            if not cursor or ident > 20:
                return tools
    finally:
        client.close()


def _decode_body(raw, content_type, ident):
    """A streamable-HTTP reply is either a JSON document or an event stream; return the reply for `ident`."""
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" in (content_type or ""):
        for block in text.split("\n\n"):
            data = "".join(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
            if data:
                try:
                    message = json.loads(data)
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("id") == ident:
                    return message
        raise McpError("the event stream did not contain the reply")
    try:
        message = json.loads(text)
    except ValueError:
        raise McpError(f"the server did not return JSON: {text[:120]!r}")
    if isinstance(message, list):
        message = next((m for m in message if isinstance(m, dict) and m.get("id") == ident), {})
    if "error" in message:
        raise McpError(f"the server answered with an error: {json.dumps(message['error'])[:200]}")
    return message


def tools_over_http(definition, timeout=20):
    url = expand(definition["url"], "url")
    if not url.startswith(("http://", "https://")):
        raise McpError("the url must start with http:// or https://")
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    for key, value in (definition.get("headers") or {}).items():
        headers[str(key)] = expand(str(value), f"headers.{key}")
    session = {}

    def post(message, wait):
        request = urllib.request.Request(url, data=json.dumps(message).encode("utf-8"), headers={**headers, **session}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.headers.get("Mcp-Session-Id"):
                    session["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
                body = response.read(MAX_REPLY + 1)
                if len(body) > MAX_REPLY:
                    raise McpError("the server sent too much data")
                return _decode_body(body, response.headers.get("Content-Type"), message.get("id")) if wait else None
        except urllib.error.HTTPError as error:
            raise McpError(f"HTTP {error.code} from the server")
        except (urllib.error.URLError, OSError) as error:
            raise McpError(f"cannot reach the server: {getattr(error, 'reason', error)}")

    post(INIT, True)
    post(INITIALIZED, False)
    return _collect(post(_request("tools/list", 2, {}), True))


def list_tools(definition, timeout=20):
    if definition.get("url"):
        return tools_over_http(definition, timeout)
    if definition.get("command"):
        return tools_over_stdio(definition, timeout)
    raise McpError("the server has neither a command nor a url")
