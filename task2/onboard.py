#!/usr/bin/env python3
"""
Balto Day-1 onboarding planner.

Reads new_hires.csv, resolves role/department → Okta/Google/Iru assignments
using an explicit mapping table (messy RBAC assumed), flags rows that need
human attention, and drafts a Slack welcome via a real LLM API call.

Design choices (see submission write-up for full rationale):
- Fail loud on ambiguous / missing mappings — never silent broadest-group default.
- Mock Okta / Google / Iru / Slack HTTP shapes; only the welcome LLM call is live.
- Missing fields / bad dates → row flagged, script continues.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

try:
    from dateutil import parser as date_parser
except ImportError:  # pragma: no cover
    date_parser = None  # type: ignore


REQUIRED_FIELDS = (
    "full_name",
    "work_email",
    "role",
    "department",
    "manager_email",
    "start_date",
    "laptop_model",
    "location_country",
)


@dataclass
class HireRow:
    raw: dict[str, str]
    line_number: int
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass
class PlanBlock:
    hire: HireRow
    okta_groups: list[str]
    google_license: str
    google_groups: list[str]
    shared_drives: list[str]
    iru_blueprint: str
    country_notes: list[str]
    manager_approval_needed: list[str]
    mapping_flags: list[str]
    welcome_message: str
    mock_api_calls: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def load_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def parse_csv(path: Path) -> list[HireRow]:
    rows: list[HireRow] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise SystemExit("ERROR: CSV has no header row")
        for i, raw in enumerate(reader, start=2):  # header is line 1
            cleaned = {k: (v or "").strip() for k, v in raw.items() if k}
            hire = HireRow(raw=cleaned, line_number=i)
            for field_name in REQUIRED_FIELDS:
                if not cleaned.get(field_name):
                    hire.issues.append(f"missing required field: {field_name}")
            start = cleaned.get("start_date", "")
            if start and not _parse_date(start):
                hire.issues.append(
                    f"unparseable start_date '{start}' "
                    "(expected ISO YYYY-MM-DD or common locale formats)"
                )
            country = cleaned.get("location_country", "").upper()
            if country and len(country) != 2:
                hire.issues.append(
                    f"location_country '{cleaned.get('location_country')}' "
                    "is not a 2-letter ISO code"
                )
            rows.append(hire)
    return rows


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        pass
    if date_parser is not None:
        try:
            # dayfirst=False: US-style in sample; ambiguous dates still flagged upstream
            return date_parser.parse(value, dayfirst=False).date()
        except (ValueError, OverflowError, TypeError):
            return None
    return None


# ---------------------------------------------------------------------------
# Mapping resolution — bail loudly, never silent broadest default
# ---------------------------------------------------------------------------

def resolve_plan(hire: HireRow, mapping: dict[str, Any]) -> PlanBlock:
    flags: list[str] = []
    role = hire.raw.get("role", "")
    dept = hire.raw.get("department", "")
    country = hire.raw.get("location_country", "").upper()

    base = list(mapping.get("base_groups") or [])
    dept_cfg = (mapping.get("departments") or {}).get(dept)
    role_cfg = (mapping.get("roles") or {}).get(role)

    if dept_cfg is None:
        flags.append(
            f"NO_DEPARTMENT_MAPPING: department '{dept}' is not in role_mapping.yaml "
            "— refusing to invent groups. Add the department or route to IT manually."
        )
    if role_cfg is None:
        flags.append(
            f"NO_ROLE_MAPPING: role '{role}' is not in role_mapping.yaml — "
            "bail loudly rather than defaulting to a broad group."
        )

    okta: list[str] = list(base)
    google_groups: list[str] = []
    shared_drives: list[str] = []
    iru = "UNASSIGNED — requires IT review"
    license_type = "UNASSIGNED — requires IT review"
    manager_approval: list[str] = []

    if dept_cfg:
        okta.extend(dept_cfg.get("okta_groups") or [])
        google_groups.extend(dept_cfg.get("google_groups") or [])
        shared_drives.extend(dept_cfg.get("shared_drives") or [])
        iru = dept_cfg.get("iru_blueprint") or iru
        license_type = dept_cfg.get("workspace_license") or license_type

    if role_cfg:
        # Role groups replace department okta specialty groups where listed
        role_groups = role_cfg.get("okta_groups") or []
        if role_groups:
            # Keep base + role-specific; drop dept specialty to avoid double-grant drift
            okta = list(base) + list(role_groups)
        manager_approval.extend(role_cfg.get("requires_manager_approval") or [])
        ambiguous = role_cfg.get("ambiguous_okta_groups") or []
        if ambiguous:
            flags.append(
                "AMBIGUOUS_OKTA_GROUPS: role maps to multiple historically plausible "
                f"groups {ambiguous}. Do NOT auto-add. IT must pick one and clean "
                "up the duplicate in Okta (temp-final-v2 hygiene)."
            )

    country_cfg = (mapping.get("countries") or {}).get(country)
    country_notes = list((country_cfg or {}).get("notes") or [])
    if country and country_cfg is None:
        flags.append(
            f"UNKNOWN_COUNTRY: '{country}' not in mapping — flag for People Ops / "
            "legal review before shipping hardware or granting regional systems."
        )
        country_notes = [
            "No country playbook entry — escalate before Day-1 hardware / HR access."
        ]

    # Deduplicate while preserving order
    okta = list(dict.fromkeys(okta))
    google_groups = list(dict.fromkeys(google_groups))
    shared_drives = list(dict.fromkeys(shared_drives))
    manager_approval = list(dict.fromkeys(manager_approval))

    mock_calls = build_mock_api_calls(
        hire, okta, google_groups, shared_drives, iru, license_type, manager_approval
    )

    return PlanBlock(
        hire=hire,
        okta_groups=okta,
        google_license=license_type,
        google_groups=google_groups,
        shared_drives=shared_drives,
        iru_blueprint=iru,
        country_notes=country_notes,
        manager_approval_needed=manager_approval,
        mapping_flags=flags,
        welcome_message="",  # filled later
        mock_api_calls=mock_calls,
    )


def build_mock_api_calls(
    hire: HireRow,
    okta_groups: list[str],
    google_groups: list[str],
    shared_drives: list[str],
    iru: str,
    license_type: str,
    manager_approval: list[str],
) -> list[dict[str, Any]]:
    """Payload shapes that mirror real Okta / Workspace Admin / Iru / Slack APIs."""
    email = hire.raw.get("work_email", "")
    name = hire.raw.get("full_name", "")
    calls: list[dict[str, Any]] = [
        {
            "system": "okta",
            "method": "POST",
            "path": "/api/v1/users",
            "payload": {
                "profile": {
                    "firstName": name.split(" ", 1)[0] if name else "",
                    "lastName": name.split(" ", 1)[-1] if name else "",
                    "email": email,
                    "login": email,
                    "managerId": hire.raw.get("manager_email"),
                    "department": hire.raw.get("department"),
                    "title": hire.raw.get("role"),
                    "countryCode": hire.raw.get("location_country", "").upper(),
                },
                "groupIds": okta_groups,  # resolved to IDs in production
                "activate": True,
            },
            "notes": "In production: create user, then POST /users/{id}/groups/{groupId} per group.",
        },
        {
            "system": "google_workspace",
            "method": "POST",
            "path": "directory.googleapis.com/admin/directory/v1/users",
            "payload": {
                "primaryEmail": email,
                "name": {"fullName": name},
                "password": "{{temporary_password_from_secrets_manager}}",
                "changePasswordAtNextLogin": True,
                "orgUnitPath": f"/Employees/{hire.raw.get('department', 'Unassigned')}",
            },
            "license": license_type,
            "group_memberships": google_groups,
            "shared_drives": shared_drives,
        },
        {
            "system": "iru",
            "method": "POST",
            "path": "/api/v1/blueprints/assign",
            "payload": {
                "blueprint_name": iru,
                "device_user_email": email,
                "laptop_model": hire.raw.get("laptop_model"),
            },
        },
    ]
    if manager_approval:
        calls.append(
            {
                "system": "slack",
                "method": "POST",
                "path": "chat.postMessage",
                "payload": {
                    "channel": "#it-approvals",
                    "text": (
                        f"Manager approval needed for {email}: "
                        f"{', '.join(manager_approval)}"
                    ),
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"*Elevated access pending*\n"
                                    f"Hire: {name} (`{email}`)\n"
                                    f"Manager: {hire.raw.get('manager_email')}\n"
                                    f"Groups: {', '.join(manager_approval)}"
                                ),
                            },
                        }
                    ],
                },
                "notes": "Do NOT auto-add these groups; wait for manager approve action.",
            }
        )
    return calls


# ---------------------------------------------------------------------------
# LLM welcome message (real API call)
# ---------------------------------------------------------------------------

WELCOME_SYSTEM = """You write short Slack welcome DMs for new Balto Software hires.
Balto is a remote SaaS company. Tone: warm, concise, practical. 4–6 sentences max.
Include: welcome by first name, Day-1 IT checklist pointer (#it-help), who their manager is,
and one concrete tip (Okta SSO is the front door; don't DM the founder for access).
Do not invent benefits, salary, or confidential policy. No emojis overload — at most one.
"""


def draft_welcome(hire: HireRow, dry_run: bool = False) -> str:
    first = (hire.raw.get("full_name") or "there").split()[0]
    user_prompt = textwrap.dedent(
        f"""
        Write a Slack DM welcome for:
        - Name: {hire.raw.get('full_name')}
        - Role: {hire.raw.get('role')}
        - Department: {hire.raw.get('department')}
        - Manager email: {hire.raw.get('manager_email') or 'TBD — manager missing in feed'}
        - Start date: {hire.raw.get('start_date')}
        - Country: {hire.raw.get('location_country')}
        """
    ).strip()

    if dry_run:
        return (
            f"[DRY-RUN TEMPLATE] Hey {first} — welcome to Balto! I'm IT Ops. "
            f"Your manager is {hire.raw.get('manager_email') or 'being confirmed'}. "
            "Day-1: enroll in Okta, check #it-help for the checklist, and submit access "
            "via the Balto IT Request form (not Slack DMs). Ping me if your laptop "
            "MDM enrollment stalls."
        )

    provider = (os.getenv("LLM_PROVIDER") or "openai").lower()
    if provider == "openai":
        return _openai_welcome(user_prompt)
    if provider in {"anthropic", "claude"}:
        return _anthropic_welcome(user_prompt)
    if provider in {"gemini", "google"}:
        return _gemini_welcome(user_prompt)
    raise SystemExit(f"Unsupported LLM_PROVIDER={provider}")


def _openai_welcome(user_prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(
            "OPENAI_API_KEY not set. Export it, or run with --dry-run-llm "
            "for a marked template (assessment prefers a real call)."
        )
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.4,
        messages=[
            {"role": "system", "content": WELCOME_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _anthropic_welcome(user_prompt: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set (or use --dry-run-llm).")
    import urllib.request

    body = json.dumps(
        {
            "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            "max_tokens": 300,
            "system": WELCOME_SYSTEM,
            "messages": [{"role": "user", "content": user_prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    parts = [p.get("text", "") for p in data.get("content", []) if p.get("type") == "text"]
    return "\n".join(parts).strip()


def _gemini_welcome(user_prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set (or use --dry-run-llm).")
    import urllib.request

    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    body = json.dumps(
        {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": WELCOME_SYSTEM + "\n\n" + user_prompt}],
                }
            ]
        }
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Gemini response: {data}") from exc


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_block(plan: PlanBlock) -> str:
    h = plan.hire
    lines = [
        "=" * 72,
        f"EMPLOYEE: {h.raw.get('full_name')} <{h.raw.get('work_email')}>  (CSV line {h.line_number})",
        f"Role / Dept: {h.raw.get('role')} / {h.raw.get('department')}",
        f"Manager: {h.raw.get('manager_email') or 'MISSING'}",
        f"Start: {h.raw.get('start_date')} | Laptop: {h.raw.get('laptop_model')} | "
        f"Country: {h.raw.get('location_country')}",
    ]
    if h.issues:
        lines.append("INPUT ISSUES:")
        for issue in h.issues:
            lines.append(f"  - {issue}")
        lines.append("STATUS: SKIPPED automations — fix feed row before provisioning.")
        lines.append("=" * 72)
        return "\n".join(lines)

    def bullets(items: list[str]) -> list[str]:
        return [f"  - {x}" for x in items] if items else ["  - (none)"]

    lines.extend(
        [
            "",
            "Okta groups (auto-grant candidates):",
            *bullets(plan.okta_groups),
            "",
            f"Google Workspace license: {plan.google_license}",
            "Google groups / DLs:",
            *bullets(plan.google_groups),
            "Shared drives:",
            *bullets(plan.shared_drives),
            "",
            f"Iru blueprint: {plan.iru_blueprint}",
            "",
            "Country considerations:",
            *bullets(plan.country_notes),
            "",
            "REQUIRES MANAGER APPROVAL (do NOT auto-grant):",
            *bullets(plan.manager_approval_needed),
            "",
            "Mapping flags:",
            *bullets(plan.mapping_flags),
            "",
            "Slack welcome draft:",
            textwrap.indent(plan.welcome_message or "(empty)", "  "),
            "",
            "Mock API calls (payload shapes):",
            textwrap.indent(json.dumps(plan.mock_api_calls, indent=2), "  "),
            "=" * 72,
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Balto Day-1 onboarding planner")
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path(__file__).with_name("new_hires.csv"),
        help="Path to new_hires.csv",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=Path(__file__).with_name("role_mapping.yaml"),
        help="Path to role_mapping.yaml",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to also write full output",
    )
    parser.add_argument(
        "--dry-run-llm",
        action="store_true",
        help="Use marked template instead of a live LLM call",
    )
    args = parser.parse_args(argv)

    mapping = load_mapping(args.mapping)
    hires = parse_csv(args.csv)
    blocks: list[str] = []
    summary = {"ok": 0, "flagged_input": 0, "flagged_mapping": 0}

    for hire in hires:
        if hire.issues:
            summary["flagged_input"] += 1
            plan = PlanBlock(
                hire=hire,
                okta_groups=[],
                google_license="",
                google_groups=[],
                shared_drives=[],
                iru_blueprint="",
                country_notes=[],
                manager_approval_needed=[],
                mapping_flags=[],
                welcome_message="",
                mock_api_calls=[],
            )
            blocks.append(render_block(plan))
            continue

        plan = resolve_plan(hire, mapping)
        if plan.mapping_flags:
            summary["flagged_mapping"] += 1
        else:
            summary["ok"] += 1

        # Still draft welcome even when mapping is ambiguous — hiring manager
        # still needs a DM; provisioning stays blocked by flags.
        try:
            plan.welcome_message = draft_welcome(hire, dry_run=args.dry_run_llm)
        except Exception as exc:  # noqa: BLE001 — surface and continue other rows
            plan.welcome_message = f"[LLM ERROR] {exc}"
            plan.mapping_flags.append(f"LLM_WELCOME_FAILED: {exc}")

        blocks.append(render_block(plan))

    header = textwrap.dedent(
        f"""
        Balto IT Ops — Day-1 onboarding plan
        Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}
        CSV: {args.csv}
        Mapping: {args.mapping}
        LLM: {"dry-run template" if args.dry_run_llm else (os.getenv("LLM_PROVIDER") or "openai")}
        Summary: {summary["ok"]} clean | {summary["flagged_input"]} bad input | {summary["flagged_mapping"]} mapping flags
        """
    ).strip()
    output = header + "\n\n" + "\n\n".join(blocks) + "\n"
    sys.stdout.write(output)
    if args.out:
        args.out.write_text(output, encoding="utf-8")
        print(f"\nWrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
