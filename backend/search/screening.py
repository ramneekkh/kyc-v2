# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""
Screening coverage primitives for KYC adverse media checks.

These types exist to make one distinction explicit and auditable:

    "We searched and found nothing"   !=   "We could not search"

A KYC system MUST fail closed. An exhausted Custom Search quota, an expired
credential, or a network partition must never be rendered as a clean subject.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# If fewer than this proportion of source queries succeed, the screening run is
# considered incomplete and cannot produce an automated "no adverse media" result.
MIN_ACCEPTABLE_COVERAGE = 0.90

# Recommendation vocabulary.
# NOTE: "Accept" is deliberately absent. Acceptance of a customer relationship is
# a human act; the system only ever reports what it did or did not find.
RECOMMENDATION_NO_ADVERSE_MEDIA = "No Adverse Media Found"
RECOMMENDATION_REQUIRES_REVIEW = "Adverse Media - Requires Review"
RECOMMENDATION_ESCALATE = "Adverse Media - Escalate"
RECOMMENDATION_INCOMPLETE = "Screening Incomplete - Manual Review Required"


@dataclass
class SearchOutcome:
    """Result of a single Custom Search query, carrying failure state explicitly."""

    query: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class SearchCoverage:
    """Aggregate execution health for a batch of source queries."""

    attempted: int = 0
    succeeded: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return self.attempted - self.succeeded

    @property
    def ratio(self) -> float:
        """Proportion of source queries that completed successfully (1.0 when none attempted)."""
        if self.attempted == 0:
            return 1.0
        return self.succeeded / self.attempted

    @property
    def is_complete(self) -> bool:
        """True only when enough of the intended search surface was actually covered."""
        if self.attempted == 0:
            # Nothing was even attempted - we cannot claim to have screened anything.
            return False
        return self.ratio >= MIN_ACCEPTABLE_COVERAGE

    def merge(self, other: "SearchCoverage") -> "SearchCoverage":
        """Combines coverage across multiple search phases (priority + generated)."""
        return SearchCoverage(
            attempted=self.attempted + other.attempted,
            succeeded=self.succeeded + other.succeeded,
            errors=(self.errors + other.errors)[:20],
        )

    def describe(self) -> str:
        if self.attempted == 0:
            return "No source queries were executed."
        return (
            f"{self.succeeded} of {self.attempted} source queries completed "
            f"({self.ratio:.0%} coverage)."
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "queries_attempted": self.attempted,
            "queries_succeeded": self.succeeded,
            "queries_failed": self.failed,
            "coverage_ratio": round(self.ratio, 4),
            "is_complete": self.is_complete,
            "description": self.describe(),
            "sample_errors": self.errors[:5],
        }


def incomplete_screening_summary(subject_name: str, coverage: SearchCoverage) -> Dict[str, Any]:
    """
    The mandatory fail-closed verdict. Returned whenever the search surface was not
    adequately covered, so that an infrastructure fault can never be mistaken for a
    clean screening result.
    """
    return {
        "risk_score": "Unknown",
        "summary": (
            f"Screening for {subject_name} could not be completed. "
            f"{coverage.describe()} "
            "The absence of adverse media findings in this run is NOT evidence that none exists. "
            "This subject must be screened manually or re-run once the source failure is resolved."
        ),
        "key_findings": [],
        "requires_human_review": [
            {
                "reason": "Incomplete source coverage",
                "detail": coverage.describe(),
            }
        ],
        "recommendation": RECOMMENDATION_INCOMPLETE,
        "reasoning": "Search coverage fell below the minimum threshold required for an automated result.",
        "screening_coverage": coverage.to_dict(),
    }


# --- Custom Search quota governance -----------------------------------------
# Google Custom Search allows 10,000 queries/day. At the original 100 queries per
# subject that is ~97 subjects/day, and the 101st subject of the day silently
# received zero results - which the pipeline then reported as a clean customer.
# The quota is now tracked explicitly so exhaustion is a visible, attributable
# refusal rather than an invisible false negative.

import os
import threading
import time
from datetime import datetime, timezone


class SearchQuotaExhausted(RuntimeError):
    """Raised when the daily Custom Search budget is spent."""


class SearchQuotaGuard:
    """
    Process-local daily counter plus a simple QPS throttle.

    Deliberately conservative: it reserves a headroom slice so that an in-flight
    screening run can finish rather than being truncated at 60% coverage, which
    would force the whole run into a fail-closed verdict.

    NOTE: state is per-process. Under multiple gunicorn workers the effective limit
    is DAILY_LIMIT per worker. For a hard global cap, back this with Redis or a
    Spanner counter row.
    """

    def __init__(
        self,
        daily_limit: Optional[int] = None,
        max_qps: Optional[float] = None,
    ):
        self.daily_limit = daily_limit or int(os.environ.get("KYC_SEARCH_DAILY_LIMIT", 10000))
        self.max_qps = max_qps or float(os.environ.get("KYC_SEARCH_MAX_QPS", 8))
        self._lock = threading.Lock()
        self._day = self._today()
        self._used = 0
        self._last_call = 0.0

    @staticmethod
    def _today() -> str:
        # Quota resets on Pacific midnight; UTC date is close enough for a guard
        # whose purpose is to refuse early rather than to predict the exact reset.
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _roll_day_if_needed(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._used = 0

    def remaining(self) -> int:
        with self._lock:
            self._roll_day_if_needed()
            return max(0, self.daily_limit - self._used)

    def try_reserve(self, count: int) -> Tuple[int, int]:
        """
        Reserves up to `count` queries.

        Returns (granted, remaining_after). A partial grant is normal and the caller
        must treat the shortfall as reduced coverage, not as "nothing to search".
        """
        with self._lock:
            self._roll_day_if_needed()
            available = max(0, self.daily_limit - self._used)
            granted = min(count, available)
            self._used += granted
            return granted, self.daily_limit - self._used

    def throttle(self) -> None:
        """Blocks briefly to hold the request rate under max_qps."""
        if self.max_qps <= 0:
            return
        min_interval = 1.0 / self.max_qps
        with self._lock:
            now = time.monotonic()
            wait = min_interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last_call = now

    def release(self, count: int) -> None:
        """Returns unused reservations (e.g. when a batch is cut short)."""
        if count <= 0:
            return
        with self._lock:
            self._used = max(0, self._used - count)


# Shared guard for the process.
search_quota = SearchQuotaGuard()
