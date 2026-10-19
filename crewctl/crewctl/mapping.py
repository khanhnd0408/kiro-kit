"""Turn an AgentSpec into the two things Kiro Crew needs.

1. The Kiro agent file (~/.kiro/agents/<kiro_agent>.json): prompt, tools, model, MCP servers.
2. The crew record (an entry under `agents` in Crew's config.json): which agent file it boots,
   its workspace, display name, description and triggers.

The model is pinned in the agent file only. The crew record's own `model` stays empty, which
Crew documents as "inherit the bound Kiro agent's pinned model". One place to change it.
"""

from .util import pretty_json


def to_kiro_agent(spec):
    """Return the Kiro agent JSON as a dict. Keys are only written when the config sets them,
    except the ones Crew's own agent files always carry (model, tools, mcpServers, includeMcpJson)."""
    agent = {"name": spec.kiro_agent}
    if spec.description:
        agent["description"] = spec.description
    agent["model"] = spec.model or "auto"
    agent["prompt"] = spec.prompt
    agent["tools"] = list(spec.tools)
    if spec.allowed_tools:
        agent["allowedTools"] = list(spec.allowed_tools)
    agent["mcpServers"] = dict(spec.mcp_servers)
    agent["includeMcpJson"] = spec.include_mcp_json
    if spec.resources:
        agent["resources"] = list(spec.resources)
    if spec.permissions:
        agent["permissions"] = spec.permissions
    if spec.hooks:
        agent["hooks"] = spec.hooks
    agent.update(spec.extra_agent_fields)
    return agent


def kiro_agent_bytes(spec):
    return pretty_json(to_kiro_agent(spec)).encode("utf-8")


def to_registration(spec):
    """The crew record this spec wants. Compared against Crew's config.json by `plan`."""
    return {
        "name": spec.name,
        "kiro_agent": spec.kiro_agent,
        "workspace": spec.workspace,
        "workspace_dir": spec.workspace_dir,
        "display_name": spec.display_name,
        "description": spec.description,
        "triggers": spec.triggers,
        "reasoning_effort": spec.reasoning_effort,
        "session_color": spec.session_color,
        "starred": spec.starred,
    }
