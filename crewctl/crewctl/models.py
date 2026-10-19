"""The model catalog and the checks that stop an agent from pinning a model you can't use."""

import json
import os

_CATALOG = None


def catalog():
    """Return {model id: entry} from models.json."""
    global _CATALOG
    if _CATALOG is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        _CATALOG = {entry["id"]: entry for entry in data["models"]}
    return _CATALOG


def check_model(model, plan, local_ids, allow_unknown=False):
    """Check one model id. Return a list of (level, message); level is 'error', 'warn' or 'info'.

    plan is 'free' or 'paid'. local_ids is the set of model ids in this machine's Crew
    cache (~/.kiro/crew/provider_models.json), or None if that cache is missing.
    """
    if model in ("", "auto"):
        return []
    entry = catalog().get(model)
    in_local = local_ids is not None and model in local_ids
    out = []
    if entry is None and not in_local:
        if allow_unknown:
            return [("warn", f"model '{model}' is not in the catalog or your Crew model list (allowed by settings)")]
        known = ", ".join(sorted(catalog()))
        return [("error", f"unknown model '{model}'. Known ids: {known}. If it is new, add it to the catalog or set "
                          "\"allow_unknown_models\": true in crewctl.json.")]
    if entry is not None and entry.get("status") == "end-of-life":
        out.append(("error", f"model '{model}' has reached end of life ({entry.get('source', '')})"))
    if plan == "free":
        if in_local:
            pass
        elif entry is not None and entry.get("free_tier") is True:
            pass
        elif entry is not None and entry.get("free_tier") is False:
            out.append(("error", f"model '{model}' needs a paid Kiro plan. On the Free plan the first prompt can fail. "
                                 "Pick a free model, or set \"plan\": \"paid\" in crewctl.json after upgrading."))
        else:
            out.append(("warn", f"model '{model}' is not documented as available on the Free plan and is not in your Crew "
                                "model list. Check the dashboard model picker."))
    elif local_ids is not None and not in_local:
        out.append(("warn", f"model '{model}' is not in your local Crew model list ({len(local_ids)} models). "
                            "The list may be stale; check the dashboard model picker."))
    if entry is not None and entry.get("id_status") == "by-pattern" and not in_local:
        out.append(("info", f"the spelling '{model}' is inferred from Kiro's naming pattern. Confirm it in the "
                            "dashboard model picker before relying on it."))
    return out


def format_table(plan="free", local_ids=None):
    """Return the catalog as text, cheapest first."""
    rows = sorted(catalog().values(), key=lambda e: (e["credits"] is None, e["credits"] or 0, e["id"]))
    lines = [f"{'MODEL ID':<20} {'CREDITS':>8} {'>272K':>6} {'CONTEXT':>9}  {'FREE PLAN':<9} {'IN YOUR CREW LIST':<18} STATUS"]
    for e in rows:
        credits = "?" if e["credits"] is None else f"{e['credits']:g}x"
        over = "" if e["credits_over_272k"] is None else f"{e['credits_over_272k']:g}x"
        free = {True: "yes", False: "no", None: "unknown"}[e["free_tier"]]
        local = "unknown" if local_ids is None else ("yes" if e["id"] in local_ids else "no")
        lines.append(f"{e['id']:<20} {credits:>8} {over:>6} {e['context_tokens']:>9,}  {free:<9} {local:<18} {e['status']}")
    lines.append("")
    lines.append("Credits are multipliers relative to Auto = 1.0. See docs/model-notes.md.")
    return "\n".join(lines)
