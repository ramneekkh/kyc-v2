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

"""Tests for perpetual KYC (pKYC) subject selection.

Ongoing monitoring is a standing obligation (FATF Rec. 10; MAS Notice 626 s.7),
not a best-effort background task. The previous scheduler enumerated an empty
list, so no subject was ever re-screened while the UI implied otherwise. These
tests pin the selection logic that replaced it.
"""

import datetime
import json

from backend import scheduler
from backend.scheduler import (
    MONITORING_INTERVAL_DAYS,
    _is_due,
    _risk_tier,
    select_subjects_due_for_monitoring,
)

NOW = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.timezone.utc)


def _subject(risk=None, days_ago=None, subject_id="s1", name="Subject", anchor=NOW):
    """Builds a subject row as get_all_subjects would return it.

    `anchor` defaults to the fixed NOW used by the _is_due tests. Tests that
    exercise select_subjects_due_for_monitoring must pass the real wall clock,
    because that function reads the clock itself.
    """
    summary = json.dumps({"risk_score": risk}) if risk is not None else None
    updated_at = None
    if days_ago is not None:
        updated_at = (anchor - datetime.timedelta(days=days_ago)).isoformat()
    return {
        "subject_id": subject_id,
        "subject_name": name,
        "summary": summary,
        "updated_at": updated_at,
    }


def _live_subject(**kwargs):
    """A subject dated against the real clock, for selection tests."""
    kwargs.setdefault("anchor", datetime.datetime.now(datetime.timezone.utc))
    return _subject(**kwargs)


class TestRiskTier:
    def test_high_medium_low_are_recognised(self):
        assert _risk_tier(_subject(risk="High")) == "high"
        assert _risk_tier(_subject(risk="Medium")) == "medium"
        assert _risk_tier(_subject(risk="Low")) == "low"

    def test_case_and_whitespace_insensitive(self):
        assert _risk_tier(_subject(risk="  hIgH  ")) == "high"

    def test_unknown_score_maps_to_the_tightest_cadence(self):
        # A fail-closed run emits risk_score "Unknown". That subject has NOT been
        # screened, so it is the one most in need of monitoring -- it must not
        # fall into the 30-day low-risk bucket.
        assert _risk_tier(_subject(risk="Unknown")) == "unknown"
        assert MONITORING_INTERVAL_DAYS["unknown"] <= MONITORING_INTERVAL_DAYS["low"]

    def test_missing_summary_maps_to_unknown(self):
        assert _risk_tier({"subject_id": "s1"}) == "unknown"

    def test_malformed_summary_json_does_not_crash(self):
        assert _risk_tier({"summary": "{not json"}) == "unknown"

    def test_dict_summary_is_accepted_as_well_as_json(self):
        assert _risk_tier({"summary": {"risk_score": "High"}}) == "high"


class TestIsDue:
    def test_never_screened_is_always_due(self):
        assert _is_due(_subject(risk="Low", days_ago=None), NOW) is True

    def test_high_risk_is_due_after_one_day(self):
        assert _is_due(_subject(risk="High", days_ago=1), NOW) is True

    def test_high_risk_is_not_due_the_same_day(self):
        assert _is_due(_subject(risk="High", days_ago=0), NOW) is False

    def test_low_risk_is_not_due_after_a_week(self):
        assert _is_due(_subject(risk="Low", days_ago=7), NOW) is False

    def test_low_risk_is_due_after_thirty_days(self):
        assert _is_due(_subject(risk="Low", days_ago=30), NOW) is True

    def test_medium_risk_respects_its_own_interval(self):
        assert _is_due(_subject(risk="Medium", days_ago=6), NOW) is False
        assert _is_due(_subject(risk="Medium", days_ago=7), NOW) is True

    def test_unparseable_timestamp_is_treated_as_due(self):
        # Fail towards screening, never away from it.
        assert _is_due({"summary": None, "updated_at": "not-a-date"}, NOW) is True

    def test_naive_timestamp_does_not_crash(self):
        naive = (NOW - datetime.timedelta(days=45)).replace(tzinfo=None).isoformat()
        assert _is_due({"summary": None, "updated_at": naive}, NOW) is True


class TestSelectSubjectsDueForMonitoring:
    def test_only_due_subjects_are_selected(self, monkeypatch):
        monkeypatch.setattr(
            scheduler.spanner_client,
            "get_all_subjects",
            lambda: [
                _live_subject(risk="Low", days_ago=1, subject_id="fresh"),
                _live_subject(risk="High", days_ago=5, subject_id="overdue"),
            ],
        )
        selected = select_subjects_due_for_monitoring()
        assert [s["subject_id"] for s in selected] == ["overdue"]

    def test_most_overdue_first(self, monkeypatch):
        monkeypatch.setattr(
            scheduler.spanner_client,
            "get_all_subjects",
            lambda: [
                _live_subject(risk="High", days_ago=2, subject_id="recent"),
                _live_subject(risk="High", days_ago=90, subject_id="ancient"),
                _live_subject(risk="High", days_ago=30, subject_id="middle"),
            ],
        )
        selected = select_subjects_due_for_monitoring()
        assert [s["subject_id"] for s in selected] == ["ancient", "middle", "recent"]

    def test_cap_is_enforced(self, monkeypatch):
        monkeypatch.setattr(
            scheduler.spanner_client,
            "get_all_subjects",
            lambda: [
                _live_subject(risk="High", days_ago=10 + i, subject_id=f"s{i}")
                for i in range(50)
            ],
        )
        selected = select_subjects_due_for_monitoring(limit=10)
        assert len(selected) == 10

    def test_cap_does_not_starve_the_oldest(self, monkeypatch):
        # Under a cap, the subjects that have gone longest without review must be
        # the ones that get reviewed. Otherwise a large book can leave the same
        # tail permanently unscreened.
        monkeypatch.setattr(
            scheduler.spanner_client,
            "get_all_subjects",
            lambda: [
                _live_subject(risk="High", days_ago=days, subject_id=f"d{days}")
                for days in (2, 400, 3, 200)
            ],
        )
        selected = select_subjects_due_for_monitoring(limit=2)
        assert [s["subject_id"] for s in selected] == ["d400", "d200"]

    def test_enumeration_failure_returns_empty_and_does_not_raise(self, monkeypatch):
        def boom():
            raise RuntimeError("spanner down")

        monkeypatch.setattr(scheduler.spanner_client, "get_all_subjects", boom)
        assert select_subjects_due_for_monitoring() == []

    def test_empty_book_is_handled(self, monkeypatch):
        monkeypatch.setattr(scheduler.spanner_client, "get_all_subjects", lambda: [])
        assert select_subjects_due_for_monitoring() == []
