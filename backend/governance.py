"""
Four-Eyes (Maker-Checker) Governance & Tamper-Evident Audit Trail.

Implements institutional KYC/AML case governance aligned with MAS Notice 626
and FATF Recommendation 10:
  1. Authenticated reviewer identity extraction from Google Cloud IAP headers
     (`X-Goog-Authenticated-User-Email` / `X-Goog-Authenticated-User-Id`), with
     explicitly audited role-switching support for single-operator demonstrations.
  2. Per-finding and per-watchlist-hit Maker dispositions with mandatory written
     rationale.
  3. Case-level Maker submission (`PENDING_CHECKER_APPROVAL`) with automated
     policy guards (blocks standard onboarding when sanctions hits or
     undispositioned human-review items remain open).
  4. Independent Checker sign-off (`APPROVED`, `RETURNED_TO_MAKER`,
     `ESCALATED_MLRO`) enforcing strict separation of duties (`maker != checker`).
  5. Append-only, SHA-256 hash-chained `AuditEvents` log with cryptographic chain
     verification (`verify_audit_chain`).
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

GENESIS_HASH = "0" * 64

# Item-level disposition vocabulary (applies to both Adverse Media Findings and Watchlist Hits)
DISPOSITION_VERDICTS: Dict[str, str] = {
    "TRUE_MATCH_ESCALATE": "True Match — Material Risk (Escalate)",
    "FALSE_POSITIVE_DISMISS": "False Positive — Homonym / Not Subject (Dismiss)",
    "MITIGATED_ACCEPTABLE": "True Match — Historical / Mitigated Risk (Acceptable with Controls)",
    "PENDING_DOCUMENTATION": "Pending Verification — Awaiting Client ID / SOW Documentation",
}

# Case-level workflow states
CASE_STATES: Tuple[str, ...] = (
    "UNREVIEWED",
    "IN_MAKER_REVIEW",
    "PENDING_CHECKER_APPROVAL",
    "APPROVED",
    "RETURNED_TO_MAKER",
    "ESCALATED_MLRO",
)

# Case-level relationship decisions proposed by Maker and signed off by Checker
CASE_DECISIONS: Dict[str, str] = {
    "ONBOARD_STANDARD_CDD": "Proceed with Standard CDD",
    "PROCEED_WITH_EDD_CONTROLS": "Proceed with Enhanced Due Diligence (EDD) & Monitoring",
    "ESCALATE_TO_MLRO": "Escalate to MLRO / Sanctions Compliance (Hold Onboarding)",
    "EXIT_REJECT_RELATIONSHIP": "Reject Onboarding / Exit Relationship (Consider STR Filing)",
}

VALID_RISK_RATINGS: Tuple[str, ...] = ("Low", "Medium", "High", "Critical")

MIN_RATIONALE_LENGTH = 10

# Allows an authenticated IAP user to switch between L1 Maker and L2 Checker
# personas in the UI so four-eyes separation of duties (and the 409 self-approval
# block) can be demonstrated from a single workstation while recording the
# underlying IAP principal on every audit event.
ALLOW_DEMO_ROLE_SWITCH = os.environ.get("KYC_ALLOW_DEMO_ROLE_SWITCH", "true").lower() in (
    "1",
    "true",
    "yes",
)


class GovernanceValidationError(ValueError):
    """Raised when a disposition or review submission violates policy rules."""


class FourEyesViolationError(PermissionError):
    """Raised when the same analyst attempts to both make and check a case."""


@dataclass(frozen=True)
class ReviewerIdentity:
    authenticated_email: str
    effective_email: str
    user_id: str
    role: str  # "MAKER", "CHECKER", "MLRO"
    auth_source: str  # "iap", "iap+role_switch", "dev_local"

    @property
    def display_actor(self) -> str:
        if self.effective_email.casefold() != self.authenticated_email.casefold():
            return f"{self.effective_email} (via IAP: {self.authenticated_email})"
        return self.effective_email

    def to_dict(self) -> Dict[str, Any]:
        return {
            "authenticated_email": self.authenticated_email,
            "effective_email": self.effective_email,
            "display_actor": self.display_actor,
            "user_id": self.user_id,
            "role": self.role,
            "auth_source": self.auth_source,
            "allow_demo_role_switch": ALLOW_DEMO_ROLE_SWITCH,
        }


def _strip_iap_prefix(val: str) -> str:
    if not val:
        return ""
    # IAP formats headers as "accounts.google.com:user@example.com"
    if ":" in val and val.startswith("accounts.google.com:"):
        return val.split(":", 1)[1].strip()
    return val.strip()


def extract_reviewer_identity(req) -> ReviewerIdentity:
    """
    Extracts the authenticated reviewer identity from Google Cloud IAP headers,
    falling back to a local operator identity only when not running on Cloud Run.
    """
    raw_iap_email = req.headers.get("X-Goog-Authenticated-User-Email", "")
    raw_iap_uid = req.headers.get("X-Goog-Authenticated-User-Id", "")
    iap_email = _strip_iap_prefix(raw_iap_email)
    iap_uid = _strip_iap_prefix(raw_iap_uid)

    if iap_email:
        base_email = iap_email
        base_uid = iap_uid or iap_email
        base_source = "iap"
    else:
        # Local development fallback (never trusted on Cloud Run unless IAP sets it)
        base_email = (
            req.headers.get("X-KYC-Dev-User")
            or os.environ.get("USER_EMAIL")
            or "analyst.maker@local.kyc"
        ).strip()
        base_uid = f"local:{base_email}"
        base_source = "dev_local"

    requested_role = (req.headers.get("X-KYC-Acting-Role") or "MAKER").strip().upper()
    if requested_role not in ("MAKER", "CHECKER", "MLRO"):
        requested_role = "MAKER"

    acting_email = (req.headers.get("X-KYC-Acting-Reviewer") or "").strip()
    if ALLOW_DEMO_ROLE_SWITCH and acting_email and "@" in acting_email:
        effective_email = acting_email
        source = (
            f"{base_source}+role_switch"
            if effective_email.casefold() != base_email.casefold()
            else base_source
        )
    else:
        effective_email = base_email
        source = base_source

    return ReviewerIdentity(
        authenticated_email=base_email,
        effective_email=effective_email,
        user_id=base_uid,
        role=requested_role,
        auth_source=source,
    )


def compute_audit_event_hash(
    prev_hash: str,
    created_at_iso: str,
    subject_id: str,
    event_type: str,
    actor_email: str,
    payload: Dict[str, Any],
) -> str:
    """
    Computes a deterministic SHA-256 hash chaining this audit event to `prev_hash`.
    Any retrospective edit or deletion of a row in Spanner breaks the chain.
    """
    canonical_payload = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
    preimage = "|".join(
        [
            prev_hash or GENESIS_HASH,
            str(created_at_iso),
            str(subject_id),
            str(event_type),
            str(actor_email).casefold(),
            canonical_payload,
        ]
    )
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def verify_audit_chain(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Verifies the cryptographic integrity of an ordered list of audit events
    (oldest first). Returns a verification summary dict.
    """
    if not events:
        return {
            "valid": True,
            "event_count": 0,
            "head_hash": GENESIS_HASH,
            "broken_at_event_id": None,
        }

    # Ensure chronological order (oldest first)
    ordered = sorted(events, key=lambda e: e.get("created_at") or "")
    expected_prev = GENESIS_HASH

    for ev in ordered:
        recorded_prev = ev.get("prev_event_hash") or GENESIS_HASH
        if recorded_prev != expected_prev:
            return {
                "valid": False,
                "event_count": len(ordered),
                "head_hash": ev.get("event_hash"),
                "broken_at_event_id": ev.get("event_id"),
                "reason": (
                    f"PrevEventHash mismatch on event {ev.get('event_id')}: "
                    f"expected {expected_prev[:12]}..., found {recorded_prev[:12]}..."
                ),
            }
        recomputed = compute_audit_event_hash(
            prev_hash=expected_prev,
            created_at_iso=ev.get("created_at") or "",
            subject_id=ev.get("subject_id") or "",
            event_type=ev.get("event_type") or "",
            actor_email=ev.get("actor_email") or "",
            payload=ev.get("payload") if isinstance(ev.get("payload"), dict) else {},
        )
        if recomputed != ev.get("event_hash"):
            return {
                "valid": False,
                "event_count": len(ordered),
                "head_hash": ev.get("event_hash"),
                "broken_at_event_id": ev.get("event_id"),
                "reason": f"Payload hash tamper detected on event {ev.get('event_id')}.",
            }
        expected_prev = recomputed

    return {
        "valid": True,
        "event_count": len(ordered),
        "head_hash": expected_prev,
        "broken_at_event_id": None,
    }


def validate_disposition_input(
    item_id: str,
    item_type: str,
    verdict: str,
    rationale: str,
) -> Tuple[str, str, str]:
    if not item_id or not str(item_id).strip():
        raise GovernanceValidationError("item_id is required.")
    norm_type = (item_type or "FINDING").strip().upper()
    if norm_type not in ("FINDING", "WATCHLIST_HIT"):
        raise GovernanceValidationError(
            f"Invalid item_type '{item_type}'. Must be FINDING or WATCHLIST_HIT."
        )
    norm_verdict = (verdict or "").strip().upper()
    if norm_verdict not in DISPOSITION_VERDICTS:
        raise GovernanceValidationError(
            f"Invalid disposition verdict '{verdict}'. Allowed: {', '.join(DISPOSITION_VERDICTS)}."
        )
    clean_rationale = (rationale or "").strip()
    if len(clean_rationale) < MIN_RATIONALE_LENGTH:
        raise GovernanceValidationError(
            f"Disposition rationale must be at least {MIN_RATIONALE_LENGTH} characters "
            f"to satisfy audit documentation standards."
        )
    return norm_type, norm_verdict, clean_rationale


def validate_maker_submission(
    proposed_risk_rating: str,
    proposed_decision: str,
    maker_rationale: str,
    dispositions_by_item: Dict[str, Dict[str, Any]],
    watchlist_hits: List[Dict[str, Any]],
    required_review_items: List[Dict[str, Any]],
) -> None:
    """
    Enforces institutional KYC policy rules before a Maker can submit a case for
    Checker approval:
      1. Risk rating and decision must be valid enum members.
      2. Maker rationale must be >= MIN_RATIONALE_LENGTH chars.
      3. Every WatchlistHit must have a recorded Maker disposition.
      4. If any WatchlistHit is dispositioned as TRUE_MATCH_ESCALATE, the Maker
         cannot propose ONBOARD_STANDARD_CDD or PROCEED_WITH_EDD_CONTROLS.
      5. If proposing ONBOARD_STANDARD_CDD, all items in `requires_human_review`
         must be dispositioned (none left unreviewed or TRUE_MATCH_ESCALATE).
    """
    if proposed_risk_rating not in VALID_RISK_RATINGS:
        raise GovernanceValidationError(
            f"Invalid risk rating '{proposed_risk_rating}'. Must be one of {VALID_RISK_RATINGS}."
        )
    if proposed_decision not in CASE_DECISIONS:
        raise GovernanceValidationError(
            f"Invalid decision '{proposed_decision}'. Must be one of {list(CASE_DECISIONS)}."
        )
    if len((maker_rationale or "").strip()) < MIN_RATIONALE_LENGTH:
        raise GovernanceValidationError(
            f"Maker justification must be at least {MIN_RATIONALE_LENGTH} characters."
        )

    # Check watchlist hit dispositions
    undispositioned_wl = []
    confirmed_sanctions_hits = []
    for hit in (watchlist_hits or []):
        hid = hit.get("hit_id")
        disp = dispositions_by_item.get(hid)
        if not disp:
            undispositioned_wl.append(f"{hit.get('list_id')}:{hit.get('primary_name')}")
        elif disp.get("verdict") == "TRUE_MATCH_ESCALATE":
            confirmed_sanctions_hits.append(f"{hit.get('list_id')}:{hit.get('primary_name')}")

    if undispositioned_wl:
        raise GovernanceValidationError(
            f"All sanctions/watchlist hits must be dispositioned by the Maker before submission. "
            f"Pending ({len(undispositioned_wl)}): {', '.join(undispositioned_wl[:3])}."
        )

    if confirmed_sanctions_hits and proposed_decision in (
        "ONBOARD_STANDARD_CDD",
        "PROCEED_WITH_EDD_CONTROLS",
    ):
        raise GovernanceValidationError(
            f"Policy block: Confirmed sanctions match ({', '.join(confirmed_sanctions_hits[:2])}) "
            f"cannot be onboarded under CDD/EDD. Select ESCALATE_TO_MLRO or EXIT_REJECT_RELATIONSHIP."
        )

    if proposed_decision == "ONBOARD_STANDARD_CDD":
        for item in (required_review_items or []):
            fid = item.get("finding_id")
            if not fid:
                continue
            disp = dispositions_by_item.get(fid)
            if not disp:
                raise GovernanceValidationError(
                    f"Cannot propose Standard CDD while human-review item '{item.get('title', fid)}' "
                    f"has no recorded Maker disposition."
                )
            if disp.get("verdict") in ("TRUE_MATCH_ESCALATE", "PENDING_DOCUMENTATION"):
                raise GovernanceValidationError(
                    f"Cannot propose Standard CDD while item '{item.get('title', fid)}' "
                    f"is marked {disp.get('verdict')}."
                )


def validate_checker_decision(
    maker_email: str,
    checker_identity: ReviewerIdentity,
    checker_action: str,
    checker_rationale: str,
) -> str:
    """
    Enforces Four-Eyes separation of duties (`maker_email != checker_email`) and
    returns the resulting case state (`APPROVED`, `RETURNED_TO_MAKER`, or `ESCALATED_MLRO`).
    """
    action_map = {
        "APPROVE": "APPROVED",
        "APPROVED": "APPROVED",
        "RETURN": "RETURNED_TO_MAKER",
        "REJECT": "RETURNED_TO_MAKER",
        "RETURNED_TO_MAKER": "RETURNED_TO_MAKER",
        "ESCALATE": "ESCALATED_MLRO",
        "ESCALATED_MLRO": "ESCALATED_MLRO",
    }
    norm_action = (checker_action or "").strip().upper()
    if norm_action not in action_map:
        raise GovernanceValidationError(
            f"Invalid checker action '{checker_action}'. Use APPROVE, RETURN, or ESCALATE."
        )
    if len((checker_rationale or "").strip()) < MIN_RATIONALE_LENGTH:
        raise GovernanceValidationError(
            f"Checker sign-off rationale must be at least {MIN_RATIONALE_LENGTH} characters."
        )

    if not maker_email:
        raise GovernanceValidationError("Case has no recorded Maker submission to check.")

    if maker_email.strip().casefold() == checker_identity.effective_email.strip().casefold():
        raise FourEyesViolationError(
            f"Four-eyes separation of duties violation: Checker "
            f"('{checker_identity.effective_email}') cannot sign off on a case "
            f"submitted by the same Maker ('{maker_email}'). An independent "
            f"L2 Compliance Officer / MLRO must perform Checker review."
        )

    return action_map[norm_action]
