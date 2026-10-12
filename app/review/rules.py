"""Playbook best practices: built-in rules + rules added in the UI (data/review/rules.json).

Every rule has a category, a severity and a weight. `check` says how it is evaluated:
  auto                 built-in Python check (engine.AUTO_CHECKS)
  manual               a reviewer answers pass / fail / not applicable
  text_forbidden       regex must NOT appear in step arguments / names        params: pattern, scope (args|names|all)
  text_required        regex must appear somewhere in the playbook            params: pattern
  name_pattern         playbook name must match regex                         params: pattern
  step_name_pattern    every step name must match regex                       params: pattern
  step_kind_required   at least one step of this kind                         params: kind
  step_kind_forbidden  no step of this kind                                   params: kind
  connector_forbidden  none of these connectors may be used                   params: connectors (comma-separated)
  max_steps            at most N steps                                        params: max
  field_required       playbook field must be set                             params: field (description|tags|owners|priority)
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from ..workspace import path as _ws

RULES_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "review" / "rules.json"
CATEGORIES = ("Security", "Reliability", "Performance", "Maintainability", "Governance")
SEVERITIES = {"critical": 25, "high": 12, "medium": 6, "low": 2, "info": 0}  # points deducted per finding
CHECK_TYPES = ("manual", "text_forbidden", "text_required", "name_pattern", "step_name_pattern", "step_kind_required",
               "step_kind_forbidden", "connector_forbidden", "max_steps", "field_required")
STEP_KINDS = ("trigger", "connector", "decision", "manual_input", "create_record", "update_record", "find_records",
              "set_variable", "wait", "code_snippet", "utility", "reference", "action")
FIELDS = ("description", "tags", "owners", "priority")
_lock = threading.Lock()


def _r(rid, title, category, severity, description, suggestion, rationale="", check="auto", params=None):
    return {"id": rid, "title": title, "category": category, "severity": severity, "description": description,
            "suggestion": suggestion, "rationale": rationale, "check": check, "params": params or {},
            "builtin": True, "enabled": True, "weight": 1.0}


BUILTIN = [
    # ---------------------------------------------------------------- Security
    _r("SEC-01", "No hard-coded credentials", "Security", "critical",
       "Passwords, API keys, tokens or Authorization headers are written directly into step arguments.",
       "Move the secret into the connector configuration or FortiSOAR's secure key store (Keystore) and reference it; rotate the exposed secret.",
       "Exported playbooks are shared, versioned and visible to every playbook editor; a literal secret leaks with them."),
    _r("SEC-02", "No hard-coded IPs, emails or URLs", "Security", "medium",
       "Literal IP addresses, email addresses or hostnames are embedded in step arguments.",
       "Read environment-specific values from a Configuration (Set Variable) step at the start, global variables, or the triggering record.",
       "Literal values break when moving between dev / prod tenants and can expose internal infrastructure."),
    _r("SEC-03", "Containment actions need an approval gate", "Security", "high",
       "A disruptive action (block, isolate, quarantine, disable, delete, terminate…) runs with no Manual Input / approval step before it.",
       "Add a Manual Input (approval) step before the action, or a Decision on a confidence / severity threshold, with a reject path.",
       "Automated containment on a false positive can take a business-critical host or user offline."),
    _r("SEC-04", "Mock output must not be forced on", "Security", "high",
       "A connector step has 'Use Mock Output' hard-wired to true, so it never calls the real system.",
       "Tie mock output to a global variable (e.g. {{globalVars.Demo_mode}}) that is false in production, or remove the mock.",
       "Mock results look like real responses; analysts may act on fake data."),
    _r("SEC-05", "No unauthenticated external input", "Security", "high",
       "A Manual Input step lets external people respond without signing in.",
       "Require authentication for external input links, or limit them to known recipients and a short expiry.",
       "Unauthenticated endpoints let anyone with the link drive the playbook."),
    _r("SEC-06", "Review custom code snippets", "Security", "medium",
       "The playbook runs arbitrary Python through the Code Snippet connector.",
       "Prefer a connector action or Jinja filter; if code is required, keep it short, reviewed, and free of secrets and network calls.",
       "Code snippets run with the playbook's privileges and are hard to audit."),
    _r("SEC-07", "Pick a connector configuration explicitly", "Security", "low",
       "A connector step relies on the connector's default configuration.",
       "Select the configuration (or set it from a variable) so the step doesn't silently switch tenants or credentials when the default changes.",
       "In multi-config / multi-tenant setups the default can point at the wrong environment."),
    # ---------------------------------------------------------------- Reliability
    _r("REL-01", "Handle connector failures", "Reliability", "low",
       "A connector step has neither 'Ignore errors' nor a retry (do until) loop, so one API failure stops the run.",
       "Enable 'Ignore errors' and check the result in a Decision, or add a bounded retry for transient failures.",
       "External APIs fail intermittently; unhandled failures leave records half-processed."),
    _r("REL-02", "No unreachable steps", "Reliability", "high",
       "Some steps can't be reached from the trigger (no route leads to them).",
       "Connect the steps or delete them.", "Orphaned steps are dead code that confuses maintainers and reviewers."),
    _r("REL-03", "Decisions need a default branch", "Reliability", "medium",
       "A Decision step has no default (else) branch.",
       "Mark one condition as default (else) so unexpected values still route somewhere.",
       "Without a default, an unmatched condition ends the playbook silently."),
    _r("REL-04", "Bound retry loops", "Reliability", "low",
       "A 'do until' retry loop has no retry count or a very high one (> 10).",
       "Set a small retry count with a delay, and handle the final failure.",
       "Unbounded retries hold workers and can hammer the external API."),
    _r("REL-05", "Approvals need a reject path", "Reliability", "medium",
       "A Manual Input step offers fewer than two response options, so there's no way to decline.",
       "Offer at least Approve and Reject (or equivalent) and route both.",
       "An approval that can only say yes isn't a control."),
    _r("REL-06", "Child playbooks must exist", "Reliability", "medium",
       "A Reference step calls a playbook that isn't in this upload or the knowledge graph.",
       "Include the child playbook in the export or fix the reference.",
       "A missing child playbook fails at run time."),
    _r("REL-07", "Avoid dynamic child-playbook references", "Reliability", "info",
       "A Reference step picks the child playbook with a Jinja expression.",
       "Prefer a fixed reference, or validate the computed value before the call.",
       "Dynamic references can't be traced or validated before run time."),
    # ---------------------------------------------------------------- Performance
    _r("PERF-01", "Keep playbooks small", "Performance", "medium",
       "The playbook has many steps (> 25 is hard to read, > 40 should be split).",
       "Split reusable parts into child playbooks called with Reference steps.",
       "Large playbooks are slower to load, debug and change safely."),
    _r("PERF-02", "Disable debug mode", "Performance", "medium",
       "The playbook is exported with debug mode on, which stores detailed logs for every step.",
       "Turn debug off before deploying to production.", "Debug logging bloats the execution history and can store sensitive data."),
    _r("PERF-03", "Filter record-update triggers", "Performance", "medium",
       "The playbook triggers on record create/update with no field-based condition.",
       "Add trigger conditions so it only runs when the relevant fields change.",
       "Unfiltered update triggers run on every edit, including the playbook's own updates (loops)."),
    _r("PERF-04", "Avoid long waits", "Performance", "low",
       "A Wait step pauses for an hour or more.",
       "Use a scheduled playbook or a record-update trigger instead of a long in-flight wait.",
       "Long waits keep executions open and are lost on restarts."),
    _r("PERF-05", "Parallelise large loops", "Performance", "info",
       "A loop (for each) calls a connector sequentially.",
       "Enable parallel execution for independent iterations, or use a bulk API action.",
       "Sequential connector calls in loops are the most common cause of slow playbooks."),
    # ---------------------------------------------------------------- Maintainability
    _r("MNT-01", "Describe the playbook", "Maintainability", "medium",
       "The playbook has no description.", "Say what triggers it, what it does, and what it changes.",
       "Descriptions are what analysts and reviewers see first."),
    _r("MNT-02", "Name steps meaningfully", "Maintainability", "low",
       "Step names are generic (e.g. 'Step Output', 'Copy of …') or duplicated.",
       "Name each step after what it does (\"Get VirusTotal file report\").",
       "Step names are used in Jinja (vars.steps.<name>) and in execution logs."),
    _r("MNT-03", "Tag the playbook", "Maintainability", "low",
       "The playbook has no tags.", "Add tags for use case, data source and team so it can be found and filtered.",
       "Tags drive filtering in the playbook list and reporting."),
    _r("MNT-04", "Use a Configuration step", "Maintainability", "low",
       "A larger playbook has no Set Variable (configuration) step near the start.",
       "Collect tunable values (thresholds, team names, email lists) in a Configuration step right after the trigger.",
       "Central configuration avoids hunting through steps when moving between environments."),
    _r("MNT-05", "Use a clear playbook name", "Maintainability", "low",
       "The playbook name is very short or a default ('New Playbook', 'Copy of …').",
       "Use a descriptive, consistent name (e.g. '<Use case> - <Action>').", ""),
    _r("MNT-06", "Document steps", "Maintainability", "info",
       "Most steps have no description.", "Describe non-obvious steps, especially decisions and data transformations.", ""),
    _r("MNT-07", "No environment-specific record IDs", "Maintainability", "low",
       "Step arguments reference specific record IRIs (/api/3/<module>/<uuid>) that only exist in one environment.",
       "Look records up by a stable field (name, ID) or pass them in from the trigger.",
       "Record UUIDs differ between dev, test and prod; the playbook breaks after import."),
    _r("MNT-08", "Active state is intentional", "Maintainability", "info",
       "The playbook is exported as inactive.", "Confirm it should be inactive, or activate it after import.", ""),
    # ---------------------------------------------------------------- Governance (manual checklist)
    _r("GOV-01", "Tested in a non-production environment", "Governance", "medium",
       "The playbook was run end-to-end with test data (or mock output) outside production.",
       "Test it in a staging tenant or with Demo mode before deployment.", check="manual"),
    _r("GOV-02", "Owner and failure notification", "Governance", "medium",
       "An owning team is assigned and someone is notified when the playbook fails.",
       "Set owners on the playbook and add failure notifications (email / chat) or monitoring.", check="manual"),
    _r("GOV-03", "Matches the documented use case", "Governance", "low",
       "The behaviour was checked against the runbook / use-case document.",
       "Link the runbook (Internal docs) and walk through it with the SOC lead.", check="manual"),
    _r("GOV-04", "Rollback plan for containment", "Governance", "medium",
       "There is a documented way to undo containment actions (unblock, re-enable, release).",
       "Add an unblock / release playbook or a documented manual procedure.", check="manual"),
]
BUILTIN_IDS = {r["id"] for r in BUILTIN}


def _load() -> dict:
    try:
        data = json.loads(_ws(RULES_FILE).read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("custom", [])
    data.setdefault("overrides", {})
    data.setdefault("version", 0)
    return data


def _save(data: dict) -> None:
    _ws(RULES_FILE).parent.mkdir(parents=True, exist_ok=True)
    data["version"] = int(data.get("version", 0)) + 1
    tmp = _ws(RULES_FILE).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(_ws(RULES_FILE))


def version() -> int:
    return _load()["version"]


def all_rules(include_disabled: bool = True) -> list[dict]:
    data = _load()
    out = []
    for rule in BUILTIN:
        r = {**rule, **{k: v for k, v in data["overrides"].get(rule["id"], {}).items()
                        if k in ("enabled", "weight", "severity")}}
        out.append(r)
    out += [{**r, "builtin": False} for r in data["custom"]]
    return out if include_disabled else [r for r in out if r.get("enabled", True)]


def _validate(rule: dict) -> dict:
    clean = {
        "title": str(rule.get("title") or "").strip()[:120],
        "category": rule.get("category") or "Governance",
        "severity": rule.get("severity") or "medium",
        "description": str(rule.get("description") or "").strip()[:1000],
        "suggestion": str(rule.get("suggestion") or "").strip()[:1000],
        "rationale": str(rule.get("rationale") or "").strip()[:1000],
        "check": rule.get("check") or "manual",
        "enabled": bool(rule.get("enabled", True)),
        "weight": float(rule.get("weight", 1.0) or 1.0),
    }
    if not clean["title"]:
        raise ValueError("title is required")
    if clean["category"] not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}")
    if clean["severity"] not in SEVERITIES:
        raise ValueError(f"severity must be one of {tuple(SEVERITIES)}")
    if clean["check"] not in CHECK_TYPES:
        raise ValueError(f"check must be one of {CHECK_TYPES}")
    if not 0 <= clean["weight"] <= 5:
        raise ValueError("weight must be between 0 and 5")
    params = rule.get("params") or {}
    p: dict = {}
    if clean["check"] in ("text_forbidden", "text_required", "name_pattern", "step_name_pattern"):
        pattern = str(params.get("pattern") or "")
        if not pattern or len(pattern) > 300:
            raise ValueError("pattern is required (max 300 characters)")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}")
        p["pattern"] = pattern
        p["ignore_case"] = bool(params.get("ignore_case", True))
        if clean["check"] == "text_forbidden":
            scope = params.get("scope") or "args"
            if scope not in ("args", "names", "all"):
                raise ValueError("scope must be args, names or all")
            p["scope"] = scope
    elif clean["check"] in ("step_kind_required", "step_kind_forbidden"):
        if params.get("kind") not in STEP_KINDS:
            raise ValueError(f"kind must be one of {STEP_KINDS}")
        p["kind"] = params["kind"]
    elif clean["check"] == "connector_forbidden":
        names = [c.strip() for c in str(params.get("connectors") or "").split(",") if c.strip()]
        if not names:
            raise ValueError("list at least one connector name")
        p["connectors"] = ",".join(names[:50])
    elif clean["check"] == "max_steps":
        try:
            p["max"] = max(1, int(params.get("max")))
        except (TypeError, ValueError):
            raise ValueError("max must be a whole number")
    elif clean["check"] == "field_required":
        if params.get("field") not in FIELDS:
            raise ValueError(f"field must be one of {FIELDS}")
        p["field"] = params["field"]
    elif clean["check"] == "manual":
        p["question"] = str(params.get("question") or clean["description"] or clean["title"])[:300]
    clean["params"] = p
    return clean


def add_rule(rule: dict, by: str) -> dict:
    clean = _validate(rule)
    with _lock:
        data = _load()
        if len(data["custom"]) >= 300:
            raise ValueError("too many custom rules (max 300)")
        clean.update(id=f"CUS-{uuid.uuid4().hex[:6].upper()}", created_by=by,
                     created=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        data["custom"].append(clean)
        _save(data)
    return {**clean, "builtin": False}


def update_rule(rule_id: str, changes: dict, by: str) -> dict:
    with _lock:
        data = _load()
        if rule_id in BUILTIN_IDS:  # built-ins: only enable / weight / severity can change
            ov = data["overrides"].setdefault(rule_id, {})
            if "enabled" in changes:
                ov["enabled"] = bool(changes["enabled"])
            if "weight" in changes:
                w = float(changes["weight"])
                if not 0 <= w <= 5:
                    raise ValueError("weight must be between 0 and 5")
                ov["weight"] = w
            if "severity" in changes:
                if changes["severity"] not in SEVERITIES:
                    raise ValueError(f"severity must be one of {tuple(SEVERITIES)}")
                ov["severity"] = changes["severity"]
            ov["updated_by"] = by
            _save(data)
            return next(r for r in all_rules() if r["id"] == rule_id)
        for i, r in enumerate(data["custom"]):
            if r["id"] == rule_id:
                merged = _validate({**r, **changes})
                merged.update(id=rule_id, created_by=r.get("created_by"), created=r.get("created"), updated_by=by)
                data["custom"][i] = merged
                _save(data)
                return {**merged, "builtin": False}
    raise KeyError(rule_id)


def delete_rule(rule_id: str) -> bool:
    if rule_id in BUILTIN_IDS:
        raise ValueError("built-in rules can't be deleted; disable them instead")
    with _lock:
        data = _load()
        keep = [r for r in data["custom"] if r["id"] != rule_id]
        if len(keep) == len(data["custom"]):
            return False
        data["custom"] = keep
        _save(data)
    return True


def reset_builtin(rule_id: str) -> None:
    with _lock:
        data = _load()
        if data["overrides"].pop(rule_id, None) is not None:
            _save(data)


# ---------------------------------------------------------------- import between workspaces

def _comparable(rule: dict) -> dict:
    return {k: rule.get(k) for k in ("title", "category", "severity", "description", "suggestion", "rationale", "check",
                                     "params", "enabled", "weight")}


def import_preview(from_ws: str) -> dict:
    """What another workspace's rules would add or change here."""
    from ..workspace import using

    with using(from_ws):
        theirs = _load()
    mine = _load()
    by_origin = {(r.get("origin") or {}).get("id"): r for r in mine["custom"] if (r.get("origin") or {}).get("workspace") == from_ws}
    custom = []
    for r in theirs["custom"]:
        existing = by_origin.get(r["id"])
        status = "new" if not existing else "same" if _comparable(existing) == _comparable(r) else "changed"
        custom.append({**r, "import_status": status, "local_id": existing["id"] if existing else None})
    overrides = []
    for rid, ov in theirs["overrides"].items():
        if rid not in BUILTIN_IDS:
            continue
        cur = {k: v for k, v in mine["overrides"].get(rid, {}).items() if k in ("enabled", "weight", "severity")}
        new = {k: v for k, v in ov.items() if k in ("enabled", "weight", "severity")}
        title = next(b["title"] for b in BUILTIN if b["id"] == rid)
        overrides.append({"id": rid, "title": title, "theirs": new, "mine": cur, "import_status": "same" if cur == new else "changed"})
    return {"from": from_ws, "custom": custom, "overrides": overrides}


def import_rules(from_ws: str, custom_ids: list[str], override_ids: list[str], by: str) -> dict:
    """Copy selected custom rules and built-in overrides from another workspace. Re-importing updates in place."""
    preview = import_preview(from_ws)
    pick_c = {r["id"]: r for r in preview["custom"] if r["id"] in set(custom_ids)}
    pick_o = {o["id"]: o for o in preview["overrides"] if o["id"] in set(override_ids)}
    added = updated = 0
    with _lock:
        data = _load()
        for rid, r in pick_c.items():
            clean = _validate(r)
            clean["origin"] = {"workspace": from_ws, "id": rid, "imported": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": by}
            idx = next((i for i, x in enumerate(data["custom"]) if (x.get("origin") or {}).get("workspace") == from_ws
                        and (x.get("origin") or {}).get("id") == rid), None)
            if idx is None:
                clean.update(id=f"CUS-{uuid.uuid4().hex[:6].upper()}", created_by=r.get("created_by") or by, created=r.get("created"))
                data["custom"].append(clean)
                added += 1
            else:
                clean.update(id=data["custom"][idx]["id"], created_by=data["custom"][idx].get("created_by"), created=data["custom"][idx].get("created"))
                data["custom"][idx] = clean
                updated += 1
        for rid, o in pick_o.items():
            data["overrides"][rid] = {**o["theirs"], "updated_by": by, "imported_from": from_ws}
        if pick_c or pick_o:
            _save(data)
    return {"added": added, "updated": updated, "overrides": len(pick_o)}
