"""Read-only coverage of logged personal-quote injection, not its benefit."""
from __future__ import annotations

import json
from pathlib import Path

from interactions import SCHEMA


def _counts():
    return {"delivered_model_replies": 0, "injected": 0,
            "not_injected": 0, "unknown": 0}


def _add(counts, used):
    counts["delivered_model_replies"] += 1
    key = "injected" if used is True else "not_injected" if used is False else "unknown"
    counts[key] += 1


def _rates(counts):
    total = counts["delivered_model_replies"]
    measured = counts["injected"] + counts["not_injected"]
    return {**counts, "measured": measured,
            "trace_fraction": measured / total if total else None,
            "injection_rate": counts["injected"] / measured if measured else None,
            # Missing-trace bounds, not a statistical confidence interval.
            "injection_rate_bounds": [
                counts["injected"] / total,
                (counts["injected"] + counts["unknown"]) / total,
            ] if total else None}


def personal_memory_coverage(log_dir):
    """Count unique confirmed conversational model deliveries in available logs.

    Local replies, broadcasts, platform/local fallbacks and unconfirmed sends are
    not model-reply exposures. Unknown generation/fallback metadata is excluded
    explicitly; a missing/non-boolean injection trace stays unknown, never False.
    Group/user keys are used only internally; aliases are local to this report.
    No quotes, identities or prompts are emitted, and no runtime is initialized.
    """
    diagnostics = {"files_read": 0, "malformed_lines": 0, "read_errors": 0,
                   "duplicate_deliveries": 0, "invalid_delivery_rows": 0,
                   "excluded_fallback_deliveries": 0, "unclassified_deliveries": 0}
    overall = _counts()
    routes = {route: _counts() for route in ("reply", "banter")}
    enabled = {key: _counts() for key in ("enabled", "disabled", "unknown")}
    people, seen = {}, set()
    try:
        files = sorted(p for p in Path(log_dir).iterdir() if p.suffix == ".jsonl")
    except FileNotFoundError:
        files = []
    except OSError:
        files = []
        diagnostics["read_errors"] += 1
    for path in files:
        try:
            with path.open("rb") as stream:
                diagnostics["files_read"] += 1
                for raw_line in stream:
                    try:
                        row = json.loads(raw_line.decode("utf-8"))
                    except (ValueError, TypeError):
                        diagnostics["malformed_lines"] += 1
                        continue
                    if (not isinstance(row, dict) or row.get("schema") != SCHEMA
                            or row.get("kind") != "delivery"
                            or row.get("actor") != "assistant"
                            or row.get("status") != "delivered"
                            or row.get("route") not in ("reply", "banter")):
                        continue
                    if any(not isinstance(row.get(key), str) or not row[key]
                           for key in ("group", "user", "outgoing_id")):
                        diagnostics["invalid_delivery_rows"] += 1
                        continue
                    key = (row["group"], row["outgoing_id"])
                    if key in seen:
                        diagnostics["duplicate_deliveries"] += 1
                        continue
                    seen.add(key)
                    if row.get("generation") == "local_fallback" or row.get("fallback") is True:
                        diagnostics["excluded_fallback_deliveries"] += 1
                        continue
                    if row.get("generation") != "model" or row.get("fallback") is not False:
                        diagnostics["unclassified_deliveries"] += 1
                        continue
                    used = row.get("personal_memory_used")
                    active = row.get("personal_memory_enabled")
                    active_key = ("enabled" if active is True else
                                  "disabled" if active is False else "unknown")
                    person = people.setdefault((row["group"], row["user"]), _counts())
                    for counts in (overall, routes[row["route"]], enabled[active_key], person):
                        _add(counts, used)
        except OSError:
            diagnostics["read_errors"] += 1
    return {"schema": "regular.personal-memory-coverage.v1", **diagnostics,
            "overall": _rates(overall),
            "by_route": {key: _rates(counts) for key, counts in routes.items()},
            "by_enabled": {key: _rates(counts) for key, counts in enabled.items()},
            "by_user": [{"alias": f"P{index:03d}", **_rates(counts)}
                        for index, counts in enumerate(people.values(), 1)]}
