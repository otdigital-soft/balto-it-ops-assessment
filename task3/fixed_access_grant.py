"""
Task 3 — Fixed access-grant handler (Zapier-style → reliable async flow).

Path chosen: substantial redesign (async approval + idempotency + audit log)
rather than patching the 30-second sleep. Rationale in submission write-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


# --- Assumed helpers (would be Zapier steps / shared lib in production) ---

class Okta:
    def get_user_by_email(self, email: str) -> Optional[dict]: ...
    def add_to_group(self, user_id: str, group_id: str) -> None: ...
    def find_group_by_name(self, name: str) -> Optional[dict]: ...


class Slack:
    def post(self, channel: str, text: str, blocks: list | None = None) -> dict: ...
    def dm(self, user_email: str, text: str) -> None: ...
    def get_user_by_email(self, email: str) -> Optional[dict]: ...


class Jira:
    def transition(self, key: str, status: str) -> None: ...
    def add_comment(self, key: str, body: str) -> None: ...
    def set_labels(self, key: str, labels: list[str]) -> None: ...


class AuditLog:
    def write(self, event: dict[str, Any]) -> None: ...


class ApprovalStore:
    """Persists pending approvals so retries / late reactions are idempotent."""

    def get(self, ticket_key: str) -> Optional[dict]: ...
    def put(self, ticket_key: str, record: dict) -> None: ...
    def mark_granted(self, ticket_key: str, actor: str) -> None: ...


okta = Okta()
slack = Slack()
jira = Jira()
audit = AuditLog()
approvals = ApprovalStore()

ACCESS_LABEL = "access-request"
APPROVALS_CHANNEL = "#it-approvals"
ALLOWED_APPROVER_REACTION = "thumbsup"


@dataclass
class Ticket:
    key: str
    labels: list[str]
    reporter_email: str
    summary: str
    description: str
    custom_group_id: Optional[str]  # preferred: explicit group id field
    requested_group_name: Optional[str]


def handle_new_ticket(ticket: Ticket) -> None:
    """
    Trigger: New Jira issue created
    Filter: MUST include label exactly 'access-request' (not 'any label').
    """
    # Fix: wrong filter previously matched every ticket
    if ACCESS_LABEL not in (ticket.labels or []):
        audit.write(
            {
                "ts": _now(),
                "event": "ignored_non_access_ticket",
                "ticket": ticket.key,
                "labels": ticket.labels,
            }
        )
        return

    # Idempotency: if we already started or finished this ticket, do not re-grant
    existing = approvals.get(ticket.key)
    if existing and existing.get("status") in {"granted", "pending", "denied"}:
        audit.write(
            {
                "ts": _now(),
                "event": "duplicate_trigger_ignored",
                "ticket": ticket.key,
                "prior_status": existing["status"],
            }
        )
        return

    user = okta.get_user_by_email(ticket.reporter_email)
    if user is None:
        jira.add_comment(ticket.key, "Okta user not found for reporter email. IT review required.")
        jira.transition(ticket.key, "Needs Info")
        audit.write({"ts": _now(), "event": "okta_user_missing", "ticket": ticket.key})
        return

    manager_email = (user.get("profile") or {}).get("manager")
    # Fix: contractors with no manager must NOT auto-approve
    if not manager_email:
        jira.add_comment(
            ticket.key,
            "No manager on Okta profile (common for contractors). "
            "Routing to IT for manual sponsor approval — no auto-grant.",
        )
        jira.set_labels(ticket.key, list(set(ticket.labels + ["needs-sponsor"])))
        slack.post(
            APPROVALS_CHANNEL,
            text=f":warning: {ticket.key} has no manager — IT sponsor required before grant.",
        )
        approvals.put(
            ticket.key,
            {"status": "pending", "reason": "no_manager", "user_id": user["id"]},
        )
        audit.write({"ts": _now(), "event": "held_no_manager", "ticket": ticket.key})
        return

    group = _resolve_target_group(ticket)
    if group is None:
        jira.add_comment(
            ticket.key,
            "Could not resolve a safe Okta group. Do not put group names only in Summary. "
            "Set the 'Requested Group ID' field (or exact group name custom field).",
        )
        jira.transition(ticket.key, "Needs Info")
        audit.write({"ts": _now(), "event": "group_unresolved", "ticket": ticket.key})
        return

    # Post approval request addressed to the manager (not "anyone with 👍")
    manager_slack = slack.get_user_by_email(manager_email)
    mention = f"<@{manager_slack['id']}>" if manager_slack else manager_email
    msg = slack.post(
        channel=APPROVALS_CHANNEL,
        text=f"Access approval needed for {ticket.key}",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{mention} please approve/deny Okta group "
                        f"`{group['profile']['name']}` for `{user['profile']['email']}` "
                        f"(ticket {ticket.key}).\n"
                        f"React :thumbsup: to approve, :x: to deny. "
                        f"Only {manager_email} counts."
                    ),
                },
            }
        ],
    )

    approvals.put(
        ticket.key,
        {
            "status": "pending",
            "user_id": user["id"],
            "group_id": group["id"],
            "manager_email": manager_email,
            "manager_slack_id": (manager_slack or {}).get("id"),
            "slack_channel": APPROVALS_CHANNEL,
            "slack_ts": msg["ts"],
            "created_at": _now(),
        },
    )
    jira.add_comment(ticket.key, "Approval requested in #it-approvals. Waiting on manager.")
    audit.write(
        {
            "ts": _now(),
            "event": "approval_requested",
            "ticket": ticket.key,
            "manager": manager_email,
            "group_id": group["id"],
        }
    )
    # NOTE: no time.sleep. A separate Slack reaction webhook calls handle_approval_reaction.


def handle_approval_reaction(
    channel: str,
    message_ts: str,
    reaction: str,
    reactor_slack_id: str,
) -> None:
    """Async path: Slack reaction event → grant/deny. Replaces the 30s sleep."""
    pending = _find_pending_by_slack(message_ts)
    if pending is None:
        return

    ticket_key = pending["ticket_key"]
    record = pending["record"]

    if record.get("status") == "granted":
        audit.write({"ts": _now(), "event": "idempotent_skip_already_granted", "ticket": ticket_key})
        return

    # Fix: only the named manager's reaction counts (not any channel member)
    if reactor_slack_id != record.get("manager_slack_id"):
        audit.write(
            {
                "ts": _now(),
                "event": "ignored_non_manager_reaction",
                "ticket": ticket_key,
                "reactor": reactor_slack_id,
            }
        )
        return

    if reaction == ALLOWED_APPROVER_REACTION:
        okta.add_to_group(record["user_id"], record["group_id"])
        approvals.mark_granted(ticket_key, actor=reactor_slack_id)
        jira.transition(ticket_key, "Done")
        jira.add_comment(ticket_key, f"Access granted by manager after Slack approval.")
        # Fix: DM the actual manager (was using possibly-None / wrong address before)
        slack.dm(
            record["manager_email"],
            f"FYI: you approved access on {ticket_key}; grant completed in Okta.",
        )
        audit.write(
            {
                "ts": _now(),
                "event": "access_granted",
                "ticket": ticket_key,
                "group_id": record["group_id"],
                "approver": reactor_slack_id,
            }
        )
    elif reaction in {"x", "wastebasket", "no_entry"}:
        approvals.put(ticket_key, {**record, "status": "denied"})
        jira.transition(ticket_key, "Denied")
        jira.add_comment(ticket_key, "Manager denied access via Slack reaction.")
        audit.write({"ts": _now(), "event": "access_denied", "ticket": ticket_key})


def _resolve_target_group(ticket: Ticket) -> Optional[dict]:
    """
    Fix: never treat free-text Summary as an Okta group name.
    Prefer explicit group ID custom field; fall back to exact-name custom field.
    """
    if ticket.custom_group_id:
        # In real Okta client: get_group(ticket.custom_group_id)
        return {"id": ticket.custom_group_id, "profile": {"name": ticket.custom_group_id}}
    if ticket.requested_group_name:
        return okta.find_group_by_name(ticket.requested_group_name)
    return None


def _find_pending_by_slack(message_ts: str) -> Optional[dict]:
    """Lookup helper — implementation would query ApprovalStore by slack_ts index."""
    raise NotImplementedError("wired to datastore in production Zap/Make/Apps Script")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
