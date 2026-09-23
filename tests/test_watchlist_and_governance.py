"""
Unit tests for:
  1. Watchlist & Sanctions Screening Engine (`backend/search/watchlist.py`)
  2. Four-Eyes (Maker-Checker) Governance & SHA-256 Hash-Chained Audit Trail (`backend/governance.py`)
  3. Watchlist integration with `finalize_summary` (`backend/search/utils.py`)
"""

import pytest

from backend.governance import (
    FourEyesViolationError,
    GENESIS_HASH,
    GovernanceValidationError,
    ReviewerIdentity,
    compute_audit_event_hash,
    extract_reviewer_identity,
    validate_checker_decision,
    validate_disposition_input,
    validate_maker_submission,
    verify_audit_chain,
)
from backend.search.screening import (
    RECOMMENDATION_INCOMPLETE,
    RECOMMENDATION_NO_ADVERSE_MEDIA,
    RECOMMENDATION_REQUIRES_REVIEW,
    RECOMMENDATION_SANCTIONS_HIT,
    SearchCoverage,
)
from backend.search.utils import finalize_summary
from backend.search.watchlist import (
    WATCHLIST_FEEDS,
    WatchlistCoverage,
    WatchlistIndex,
    WatchlistListStatus,
    normalize_watchlist_name,
    phonetic_key,
)


# ---------------------------------------------------------------------------
# 1. Watchlist Name Normalization, Phonetics & Scoring
# ---------------------------------------------------------------------------

class TestWatchlistMatching:

    def test_normalize_strips_corporate_suffixes_and_diacritics(self):
        a = normalize_watchlist_name("Promsvyazbank Public Joint Stock Company")
        b = normalize_watchlist_name("PROMSVYAZBANK PJSC")
        assert a == b == "promsvyazbank"

    def test_token_order_invariance(self):
        q_norm = normalize_watchlist_name("Kim Jong Un")
        e_norm = normalize_watchlist_name("Jong Un KIM")
        q_tok = tuple(sorted(q_norm.split()))
        e_tok = tuple(sorted(e_norm.split()))
        score = WatchlistIndex._compute_name_similarity(q_norm, q_tok, e_norm, e_tok)
        assert score >= 0.97

    def test_phonetic_transliteration_equivalence(self):
        assert phonetic_key("Mohammed") == phonetic_key("Muhammad")
        assert phonetic_key("Sergei") == phonetic_key("Sergey")
        assert phonetic_key("Qaddafi") == phonetic_key("Gaddafi")

    def test_dob_corroboration_exact_and_conflict(self):
        status_exact, delta_exact = WatchlistIndex._evaluate_dob(
            {"1984-01-08"}, {1984}, {"1984-01-08"}, {1984}
        )
        assert status_exact == "EXACT"
        assert delta_exact > 0

        status_mismatch, delta_mismatch = WatchlistIndex._evaluate_dob(
            {"1999-05-05"}, {1999}, {"1965-01-01"}, {1965}
        )
        assert status_mismatch == "MISMATCH"
        assert delta_mismatch < 0

    def test_watchlist_coverage_fail_closed_when_zero_or_partial(self):
        assert WatchlistCoverage().is_complete is False
        partial = WatchlistCoverage(
            lists_attempted=4,
            lists_succeeded=3,
            total_entities_indexed=1000,
        )
        assert partial.is_complete is False

        complete = WatchlistCoverage(
            lists_attempted=4,
            lists_succeeded=4,
            total_entities_indexed=27529,
        )
        assert complete.is_complete is True

    def test_four_mandatory_authority_feeds_configured(self):
        feed_ids = {f.list_id for f in WATCHLIST_FEEDS}
        assert {"SG_MAS", "US_OFAC_SDN", "UN_SC", "EU_FSF"} == feed_ids


# ---------------------------------------------------------------------------
# 2. Watchlist Integration with `finalize_summary`
# ---------------------------------------------------------------------------

class TestFinalizeSummaryWithWatchlist:

    def test_confirmed_sanctions_hit_overrides_clean_adverse_media(self):
        raw_clean = {
            "risk_score": "Low",
            "summary": "No adverse media articles found.",
            "key_findings": [],
            "requires_human_review": [],
            "recommendation": RECOMMENDATION_NO_ADVERSE_MEDIA,
            "reasoning": "Clean open-source media.",
        }
        wl_result = {
            "status": "SANCTIONS_HIT_DETECTED",
            "total_hits": 1,
            "confirmed_hits": 1,
            "strong_hits": 0,
            "potential_hits": 0,
            "hits": [
                {
                    "hit_id": "hit-ofac-1",
                    "list_id": "SG_MAS",
                    "list_name": "MAS Targeted Financial Sanctions",
                    "entity_id": "NK-123",
                    "primary_name": "Promsvyazbank Public Joint Stock Company",
                    "match_score": 1.0,
                    "match_strength": "CONFIRMED",
                    "explanation": "Matched SG_MAS designation.",
                }
            ],
            "coverage": {"is_complete": True, "description": "4/4 sanctions lists active."},
        }
        out = finalize_summary(
            raw_clean,
            review_queue=[],
            coverage=SearchCoverage(attempted=10, succeeded=10),
            watchlist_screening=wl_result,
        )
        assert out["risk_score"] == "Critical"
        assert out["recommendation"] == RECOMMENDATION_SANCTIONS_HIT
        assert any(r.get("item_type") == "WATCHLIST_HIT" for r in out["requires_human_review"])

    def test_incomplete_watchlist_coverage_forces_fail_closed(self):
        raw_clean = {
            "risk_score": "Low",
            "summary": "No adverse media articles found.",
            "key_findings": [],
            "requires_human_review": [],
            "recommendation": RECOMMENDATION_NO_ADVERSE_MEDIA,
            "reasoning": "Clean open-source media.",
        }
        wl_degraded = {
            "status": "INCOMPLETE_COVERAGE",
            "total_hits": 0,
            "confirmed_hits": 0,
            "strong_hits": 0,
            "potential_hits": 0,
            "hits": [],
            "coverage": {"is_complete": False, "description": "3/4 sanctions lists active (US_OFAC_SDN FAILED)."},
        }
        out = finalize_summary(
            raw_clean,
            review_queue=[],
            coverage=SearchCoverage(attempted=10, succeeded=10),
            watchlist_screening=wl_degraded,
        )
        assert out["risk_score"] == "Unknown"
        assert out["recommendation"] == RECOMMENDATION_INCOMPLETE


# ---------------------------------------------------------------------------
# 3. Four-Eyes (Maker-Checker) Governance & Audit Chain Verification
# ---------------------------------------------------------------------------

class _DummyRequest:
    def __init__(self, headers):
        self.headers = headers


class TestMakerCheckerGovernance:

    def test_extract_reviewer_identity_strips_iap_prefix(self):
        req = _DummyRequest({
            "X-Goog-Authenticated-User-Email": "accounts.google.com:admin@ramneekkhurana.altostrat.com",
            "X-Goog-Authenticated-User-Id": "accounts.google.com:1141048046",
        })
        rev = extract_reviewer_identity(req)
        assert rev.authenticated_email == "admin@ramneekkhurana.altostrat.com"
        assert rev.effective_email == "admin@ramneekkhurana.altostrat.com"
        assert rev.auth_source == "iap"

    def test_extract_reviewer_identity_tracks_role_switch_with_iap_principal(self):
        req = _DummyRequest({
            "X-Goog-Authenticated-User-Email": "accounts.google.com:admin@ramneekkhurana.altostrat.com",
            "X-KYC-Acting-Reviewer": "compliance.checker@ramneekkhurana.altostrat.com",
            "X-KYC-Acting-Role": "CHECKER",
        })
        rev = extract_reviewer_identity(req)
        assert rev.authenticated_email == "admin@ramneekkhurana.altostrat.com"
        assert rev.effective_email == "compliance.checker@ramneekkhurana.altostrat.com"
        assert rev.role == "CHECKER"
        assert rev.auth_source == "iap+role_switch"

    def test_disposition_requires_substantive_rationale(self):
        with pytest.raises(GovernanceValidationError):
            validate_disposition_input("item-1", "FINDING", "FALSE_POSITIVE_DISMISS", "too short")

        item_type, verdict, rationale = validate_disposition_input(
            "item-1",
            "watchlist_hit",
            "FALSE_POSITIVE_DISMISS",
            "Verified passport DOB 1988-04-12 does not match designated person born 1952.",
        )
        assert item_type == "WATCHLIST_HIT"
        assert verdict == "FALSE_POSITIVE_DISMISS"
        assert len(rationale) >= 10

    def test_maker_submission_blocks_undispositioned_watchlist_hits(self):
        wl_hits = [{"hit_id": "h1", "list_id": "SG_MAS", "primary_name": "Promsvyazbank"}]
        with pytest.raises(GovernanceValidationError, match="must be dispositioned"):
            validate_maker_submission(
                proposed_risk_rating="High",
                proposed_decision="ESCALATE_TO_MLRO",
                maker_rationale="Escalating due to potential sanctions match.",
                dispositions_by_item={},
                watchlist_hits=wl_hits,
                required_review_items=[],
            )

    def test_maker_submission_blocks_cdd_onboarding_when_sanctions_confirmed(self):
        wl_hits = [{"hit_id": "h1", "list_id": "US_OFAC_SDN", "primary_name": "Jong Un Kim"}]
        dispositions = {
            "h1": {"verdict": "TRUE_MATCH_ESCALATE", "rationale": "Exact OFAC SDN match."}
        }
        with pytest.raises(GovernanceValidationError, match="Confirmed sanctions match"):
            validate_maker_submission(
                proposed_risk_rating="Critical",
                proposed_decision="PROCEED_WITH_EDD_CONTROLS",
                maker_rationale="Attempting to onboard despite confirmed SDN hit.",
                dispositions_by_item=dispositions,
                watchlist_hits=wl_hits,
                required_review_items=[],
            )

        # Escalating to MLRO is permitted
        validate_maker_submission(
            proposed_risk_rating="Critical",
            proposed_decision="ESCALATE_TO_MLRO",
            maker_rationale="Confirmed OFAC SDN match; escalating to MLRO for rejection/STR.",
            dispositions_by_item=dispositions,
            watchlist_hits=wl_hits,
            required_review_items=[],
        )

    def test_four_eyes_blocks_same_maker_and_checker(self):
        same_checker = ReviewerIdentity(
            authenticated_email="admin@ramneekkhurana.altostrat.com",
            effective_email="ADMIN@ramneekkhurana.altostrat.com",
            user_id="123",
            role="CHECKER",
            auth_source="iap",
        )
        with pytest.raises(FourEyesViolationError, match="separation of duties violation"):
            validate_checker_decision(
                maker_email="admin@ramneekkhurana.altostrat.com",
                checker_identity=same_checker,
                checker_action="APPROVE",
                checker_rationale="Approving my own Maker submission.",
            )

    def test_four_eyes_allows_independent_checker(self):
        independent_checker = ReviewerIdentity(
            authenticated_email="admin@ramneekkhurana.altostrat.com",
            effective_email="compliance.checker@ramneekkhurana.altostrat.com",
            user_id="456",
            role="CHECKER",
            auth_source="iap+role_switch",
        )
        new_state = validate_checker_decision(
            maker_email="admin@ramneekkhurana.altostrat.com",
            checker_identity=independent_checker,
            checker_action="APPROVE",
            checker_rationale="Concur with Maker EDD controls and periodic review schedule.",
        )
        assert new_state == "APPROVED"

    def test_audit_hash_chain_detects_tampering(self):
        h1 = compute_audit_event_hash(
            GENESIS_HASH,
            "2026-09-23T07:00:00.000000Z",
            "subj-1",
            "CASE_SUBMITTED_FOR_CHECKER_APPROVAL",
            "maker@bank.com",
            {"proposed_decision": "PROCEED_WITH_EDD_CONTROLS"},
        )
        ev1 = {
            "event_id": "e1",
            "subject_id": "subj-1",
            "event_type": "CASE_SUBMITTED_FOR_CHECKER_APPROVAL",
            "actor_email": "maker@bank.com",
            "payload": {"proposed_decision": "PROCEED_WITH_EDD_CONTROLS"},
            "prev_event_hash": GENESIS_HASH,
            "event_hash": h1,
            "created_at": "2026-09-23T07:00:00.000000Z",
        }
        h2 = compute_audit_event_hash(
            h1,
            "2026-09-23T07:05:00.000000Z",
            "subj-1",
            "CHECKER_DECISION_APPROVED",
            "checker@bank.com",
            {"checker_action": "APPROVE"},
        )
        ev2 = {
            "event_id": "e2",
            "subject_id": "subj-1",
            "event_type": "CHECKER_DECISION_APPROVED",
            "actor_email": "checker@bank.com",
            "payload": {"checker_action": "APPROVE"},
            "prev_event_hash": h1,
            "event_hash": h2,
            "created_at": "2026-09-23T07:05:00.000000Z",
        }

        verified = verify_audit_chain([ev1, ev2])
        assert verified["valid"] is True
        assert verified["event_count"] == 2
        assert verified["head_hash"] == h2

        # Tamper with event 1's payload
        ev1_tampered = dict(ev1, payload={"proposed_decision": "ONBOARD_STANDARD_CDD"})
        tampered_check = verify_audit_chain([ev1_tampered, ev2])
        assert tampered_check["valid"] is False
        assert tampered_check["broken_at_event_id"] == "e1"
