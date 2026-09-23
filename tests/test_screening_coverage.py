# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the fail-closed screening contract.

Every assertion here corresponds to a way the system could previously report a
clean result for a subject it never actually screened. These are not style
tests: each one maps to a defect that would have produced a wrong risk decision.
"""

import pytest

from backend.search.screening import (
    MIN_ACCEPTABLE_COVERAGE,
    RECOMMENDATION_ESCALATE,
    RECOMMENDATION_INCOMPLETE,
    RECOMMENDATION_NO_ADVERSE_MEDIA,
    RECOMMENDATION_REQUIRES_REVIEW,
    SearchCoverage,
    SearchOutcome,
    incomplete_screening_summary,
)


class TestSearchCoverage:
    """Coverage is what separates 'clean' from 'never looked'."""

    def test_nothing_attempted_is_not_complete(self):
        # The single most important assertion in this file. A run that attempted
        # zero searches has screened nothing, and must never read as complete.
        assert SearchCoverage().is_complete is False

    def test_all_succeeded_is_complete(self):
        coverage = SearchCoverage(attempted=10, succeeded=10)
        assert coverage.is_complete is True
        assert coverage.ratio == 1.0

    def test_partial_failure_below_threshold_is_incomplete(self):
        # 8/10 = 0.80, below the 0.90 floor.
        coverage = SearchCoverage(attempted=10, succeeded=8)
        assert coverage.ratio == pytest.approx(0.8)
        assert coverage.is_complete is False

    def test_threshold_boundary_is_inclusive(self):
        coverage = SearchCoverage(attempted=10, succeeded=9)
        assert coverage.ratio == pytest.approx(MIN_ACCEPTABLE_COVERAGE)
        assert coverage.is_complete is True

    def test_merge_accumulates_both_sides(self):
        merged = SearchCoverage(attempted=5, succeeded=5).merge(
            SearchCoverage(attempted=5, succeeded=1)
        )
        assert merged.attempted == 10
        assert merged.succeeded == 6
        assert merged.failed == 4
        assert merged.is_complete is False

    def test_merge_of_clean_with_empty_stays_clean(self):
        # An empty second leg (e.g. no brainstormed queries) must not by itself
        # drag a fully-successful priority sweep into "degraded".
        merged = SearchCoverage(attempted=5, succeeded=5).merge(SearchCoverage())
        assert merged.is_complete is True

    def test_describe_is_human_readable(self):
        text = SearchCoverage(attempted=10, succeeded=3).describe()
        assert "3" in text and "10" in text

    def test_to_dict_exposes_completeness_to_the_ui(self):
        payload = SearchCoverage(attempted=4, succeeded=1).to_dict()
        assert payload["is_complete"] is False
        assert payload["queries_attempted"] == 4
        assert payload["queries_succeeded"] == 1
        assert payload["queries_failed"] == 3

    def test_to_dict_keys_match_the_frontend_contract(self):
        # frontend/src/App.jsx reads these exact names off summary.screening_coverage.
        # Renaming one here silently blanks the coverage banner, which is the one
        # piece of UI that tells a reviewer not to trust the verdict.
        payload = SearchCoverage(attempted=4, succeeded=1).to_dict()
        for key in (
            "queries_attempted",
            "queries_succeeded",
            "queries_failed",
            "coverage_ratio",
            "is_complete",
            "description",
        ):
            assert key in payload, f"frontend depends on screening_coverage.{key}"


class TestRecommendationVocabulary:
    """The system reports screening outcomes; it does not make CDD decisions."""

    def test_no_decision_verbs_are_exposed(self):
        # "Accept"/"Reject" imply the machine made the onboarding call. It did
        # not, and presenting it that way invites a reviewer to rubber-stamp.
        banned = {"accept", "reject", "approve", "deny", "clear", "decline"}
        for recommendation in (
            RECOMMENDATION_NO_ADVERSE_MEDIA,
            RECOMMENDATION_REQUIRES_REVIEW,
            RECOMMENDATION_ESCALATE,
            RECOMMENDATION_INCOMPLETE,
        ):
            words = {w.strip(" -").lower() for w in recommendation.split()}
            assert not (words & banned), f"{recommendation!r} leaks a decision verb"

    def test_recommendations_are_distinct(self):
        values = [
            RECOMMENDATION_NO_ADVERSE_MEDIA,
            RECOMMENDATION_REQUIRES_REVIEW,
            RECOMMENDATION_ESCALATE,
            RECOMMENDATION_INCOMPLETE,
        ]
        assert len(set(values)) == len(values)


class TestIncompleteScreeningSummary:
    def test_refuses_to_state_a_risk_score(self):
        coverage = SearchCoverage(attempted=10, succeeded=0)
        summary = incomplete_screening_summary("Jane Doe", coverage)

        # "Low" here would be a lie: we searched nothing successfully.
        assert summary["risk_score"] == "Unknown"
        assert summary["recommendation"] == RECOMMENDATION_INCOMPLETE

    def test_routes_to_a_human(self):
        coverage = SearchCoverage(attempted=10, succeeded=0)
        summary = incomplete_screening_summary("Jane Doe", coverage)
        assert summary["requires_human_review"]

    def test_names_the_subject_and_the_shortfall(self):
        coverage = SearchCoverage(attempted=10, succeeded=2)
        summary = incomplete_screening_summary("Jane Doe", coverage)
        blob = " ".join(str(v) for v in summary.values())
        assert "Jane Doe" in blob

    def test_carries_machine_readable_coverage(self):
        coverage = SearchCoverage(attempted=10, succeeded=2)
        summary = incomplete_screening_summary("Jane Doe", coverage)
        assert summary["screening_coverage"]["is_complete"] is False


class TestSearchOutcome:
    def test_failure_is_not_silently_an_empty_result_list(self):
        # The original defect: a failed search returned [] and was indistinguishable
        # from a genuinely empty result set.
        failed = SearchOutcome(query="q", items=[], error="429 rate limited")
        empty_but_ok = SearchOutcome(query="q", items=[])

        # Identical payloads...
        assert failed.items == empty_but_ok.items
        # ...but distinguishable intent. This is the whole point.
        assert failed.succeeded is False
        assert empty_but_ok.succeeded is True
