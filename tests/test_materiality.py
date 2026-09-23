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

"""Tests for materiality filtering and summary post-conditions.

The summary prompt *instructs* the model to follow the compliance rules.
`finalize_summary` *enforces* them. These tests exist to keep that distinction
real: if enforcement regresses to mere instruction, they fail.
"""

from backend.search.screening import (
    RECOMMENDATION_INCOMPLETE,
    RECOMMENDATION_NO_ADVERSE_MEDIA,
    SearchCoverage,
)
from backend.search.utils import (
    _safe_int,
    build_human_review_queue,
    finalize_summary,
    is_material_finding,
)


class TestSafeInt:
    """The model returns whatever it likes. Coercion must be total."""

    def test_plain_int(self):
        assert _safe_int(7) == 7

    def test_float_truncates(self):
        assert _safe_int(7.9) == 7

    def test_numeric_string(self):
        assert _safe_int("7") == 7

    def test_fraction_string_takes_the_numerator(self):
        # The model likes to answer "8/10".
        assert _safe_int("8/10") == 8

    def test_not_available_falls_back(self):
        # This exact value used to raise ValueError inside the relevance filter,
        # which corrupted the whole summary and triggered an infinite
        # regeneration loop.
        assert _safe_int("N/A") == 0

    def test_none_falls_back(self):
        assert _safe_int(None) == 0

    def test_empty_string_falls_back(self):
        assert _safe_int("") == 0

    def test_arbitrary_prose_falls_back(self):
        assert _safe_int("high risk") == 0

    def test_bool_is_not_an_int_here(self):
        # True == 1 in Python, but a boolean in a score field is a data error,
        # not a score of one.
        assert _safe_int(True) == 0

    def test_custom_default_is_honoured(self):
        assert _safe_int("N/A", default=-1) == -1

    def test_negative_string(self):
        assert _safe_int("-3") == -3

    def test_never_raises(self):
        for value in ([], {}, object(), b"5", 3 + 4j):
            _safe_int(value)  # must not raise


class TestIsMaterialFinding:
    """Over-inclusive by design: a false positive costs a minute of reading."""

    def test_explicit_non_match_is_dropped(self):
        assert is_material_finding({"match_status": "Negative", "risk_severity": 4}) is False

    def test_severe_allegation_survives_a_low_relevance_score(self):
        # The original defect. Rubric: identity 2 + severity 4 + recency 0
        # + source penalty -3 = 3, below the >= 5 gate, so a serious allegation
        # on a low-credibility source was silently discarded.
        finding = {
            "relevance_score": 3,
            "risk_severity": 4,
            "risk_level": "High",
            "match_status": "Potential",
        }
        assert is_material_finding(finding) is True

    def test_unscreened_article_reaches_the_summariser(self):
        assert is_material_finding({"analysis_status": "Failed"}) is True

    def test_unresolved_syndication_reaches_the_summariser(self):
        assert is_material_finding({"analysis_status": "Unresolved"}) is True

    def test_hitl_reaches_the_summariser(self):
        assert is_material_finding({"match_status": "HITL", "relevance_score": 0}) is True

    def test_high_score_alone_is_enough(self):
        assert is_material_finding({"relevance_score": 9}) is True

    def test_garbage_score_does_not_crash_and_does_not_pass(self):
        assert is_material_finding({"relevance_score": "N/A"}) is False

    def test_genuinely_immaterial_is_dropped(self):
        finding = {
            "relevance_score": 1,
            "risk_severity": 0,
            "risk_level": "Low",
            "match_status": "Confirmed",
        }
        assert is_material_finding(finding) is False

    def test_negative_beats_every_other_signal(self):
        # An explicit "this is not the subject" outranks a high score: keeping
        # a confirmed wrong-person hit is how false positives reach a file.
        finding = {
            "match_status": "Negative",
            "relevance_score": 10,
            "risk_level": "High",
            "risk_severity": 4,
        }
        assert is_material_finding(finding) is False


class TestHumanReviewQueue:
    def test_content_revision_gets_its_own_reason(self):
        queue = build_human_review_queue([
            {
                "is_content_revision": True,
                "match_status": "HITL",
                "source_title": "Bank fined",
                "url": "https://example.com/a",
            }
        ])
        assert len(queue) == 1
        assert "changed" in queue[0]["reason"].lower()

    def test_severe_allegation_with_weak_identity_is_queued(self):
        queue = build_human_review_queue([
            {
                "risk_severity": 4,
                "identity_confidence": 1,
                "source_title": "Fraud charges",
                "url": "https://example.com/b",
            }
        ])
        assert len(queue) == 1
        assert "identity" in queue[0]["reason"].lower()

    def test_clean_confirmed_finding_is_not_queued(self):
        queue = build_human_review_queue([
            {
                "match_status": "Confirmed",
                "risk_severity": 1,
                "identity_confidence": 4,
                "content_available": True,
                "source_title": "Company opens office",
                "url": "https://example.com/c",
            }
        ])
        assert queue == []

    def test_every_entry_carries_a_citation(self):
        queue = build_human_review_queue([
            {"analysis_status": "Failed", "url": "https://example.com/d", "source_title": "X"},
            {"match_status": "HITL", "url": "https://example.com/e", "source_title": "Y"},
        ])
        assert len(queue) == 2
        for entry in queue:
            assert entry["citations"], "a review item with no source is not actionable"


class TestFinalizeSummary:
    """Post-conditions enforced in code, not merely requested in a prompt."""

    def _complete(self):
        return SearchCoverage(attempted=10, succeeded=10)

    def _degraded(self):
        return SearchCoverage(attempted=10, succeeded=1)

    def test_degraded_coverage_overrides_a_clean_verdict(self):
        model_output = {
            "risk_score": "Low",
            "recommendation": RECOMMENDATION_NO_ADVERSE_MEDIA,
            "summary": "Nothing found.",
        }
        result = finalize_summary(model_output, [], self._degraded())

        assert result["recommendation"] == RECOMMENDATION_INCOMPLETE
        assert result["risk_score"] == "Unknown"

    def test_banned_decision_verb_is_stripped(self):
        # Even if the model ignores its instructions and emits "Accept".
        result = finalize_summary(
            {"risk_score": "Low", "recommendation": "Accept", "summary": "ok"},
            [],
            self._complete(),
        )
        assert result["recommendation"] != "Accept"
        assert "accept" not in result["recommendation"].lower()

    def test_review_queue_is_merged_in(self):
        queue = [{"reason": "Unresolved identity match", "detail": "d", "citations": ["u"]}]
        result = finalize_summary(
            {"risk_score": "Low", "recommendation": RECOMMENDATION_NO_ADVERSE_MEDIA},
            queue,
            self._complete(),
        )
        assert result["requires_human_review"]
        reasons = [i.get("reason") for i in result["requires_human_review"]]
        assert "Unresolved identity match" in reasons

    def test_clean_run_with_full_coverage_is_left_alone(self):
        result = finalize_summary(
            {
                "risk_score": "Low",
                "recommendation": RECOMMENDATION_NO_ADVERSE_MEDIA,
                "summary": "Nothing found.",
            },
            [],
            self._complete(),
        )
        assert result["recommendation"] == RECOMMENDATION_NO_ADVERSE_MEDIA
        assert result["risk_score"] == "Low"

    def test_coverage_is_attached_for_the_ui(self):
        result = finalize_summary(
            {"risk_score": "Low", "recommendation": RECOMMENDATION_NO_ADVERSE_MEDIA},
            [],
            self._degraded(),
        )
        assert result["screening_coverage"]["is_complete"] is False

    def test_empty_model_output_does_not_crash(self):
        result = finalize_summary({}, [], self._complete())
        assert isinstance(result, dict)

    def test_none_model_output_does_not_crash(self):
        result = finalize_summary(None, [], self._complete())
        assert isinstance(result, dict)
