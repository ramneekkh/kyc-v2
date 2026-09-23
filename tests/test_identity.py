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

"""Tests for subject identity derivation and content fingerprinting.

Keying a KYC subject by display name alone merges distinct customers who happen
to share a name -- and in the jurisdictions this tool targets, common names are
the norm, not the exception. These tests encode the keying hierarchy.
"""

from backend.identity import (
    content_fingerprint,
    derive_subject_id,
    extract_identity_attributes,
)


class TestDeriveSubjectId:
    def test_customer_id_is_the_preferred_key(self):
        subject_id, strategy = derive_subject_id("Tan Wei Ling", customer_id="CIF-001")
        assert strategy == "customer_id"
        assert subject_id

    def test_same_name_different_customer_ids_do_not_collide(self):
        # The headline defect: two different customers called "Tan Wei Ling"
        # were merged into one subject record, so one customer's adverse media
        # became the other's.
        a, _ = derive_subject_id("Tan Wei Ling", customer_id="CIF-001")
        b, _ = derive_subject_id("Tan Wei Ling", customer_id="CIF-002")
        assert a != b

    def test_same_customer_id_is_stable_across_calls(self):
        a, _ = derive_subject_id("Tan Wei Ling", customer_id="CIF-001")
        b, _ = derive_subject_id("Tan Wei Ling", customer_id="CIF-001")
        assert a == b

    def test_customer_id_wins_even_when_the_name_differs(self):
        # A legal-name change must not fork the customer's KYC history.
        a, _ = derive_subject_id("Tan Wei Ling", customer_id="CIF-001")
        b, _ = derive_subject_id("Tan Wei-Ling", customer_id="CIF-001")
        assert a == b

    def test_falls_back_to_composite_attributes(self):
        subject_id, strategy = derive_subject_id(
            "Tan Wei Ling", attributes={"dob": "1980-01-01", "region": "SG"}
        )
        assert strategy == "composite_attributes"
        assert subject_id

    def test_composite_attributes_discriminate_by_dob(self):
        a, _ = derive_subject_id("Tan Wei Ling", attributes={"dob": "1980-01-01"})
        b, _ = derive_subject_id("Tan Wei Ling", attributes={"dob": "1991-05-05"})
        assert a != b

    def test_name_only_is_the_last_resort(self):
        subject_id, strategy = derive_subject_id("Tan Wei Ling")
        assert strategy == "name_only"
        assert subject_id

    def test_name_only_normalises_case_and_whitespace(self):
        a, _ = derive_subject_id("Tan Wei Ling")
        b, _ = derive_subject_id("  tan   wei ling  ")
        assert a == b

    def test_name_only_normalises_accents(self):
        # "Jose" and "José" are the same person in every customer file that
        # matters; treating them as two subjects splits their risk history.
        a, _ = derive_subject_id("Jose Garcia")
        b, _ = derive_subject_id("José García")
        assert a == b

    def test_ids_are_uuid_shaped(self):
        import uuid

        subject_id, _ = derive_subject_id("Tan Wei Ling", customer_id="CIF-001")
        uuid.UUID(subject_id)  # raises if malformed

    def test_tenant_isolation(self):
        # The same CIF number in two tenants is two different people.
        a, _ = derive_subject_id("Tan Wei Ling", customer_id="CIF-001", tenant_id="bank-a")
        b, _ = derive_subject_id("Tan Wei Ling", customer_id="CIF-001", tenant_id="bank-b")
        assert a != b

    def test_blank_customer_id_is_not_treated_as_a_key(self):
        _, strategy = derive_subject_id("Tan Wei Ling", customer_id="   ")
        assert strategy != "customer_id"


class TestExtractIdentityAttributes:
    def test_picks_up_discriminating_fields(self):
        attributes = extract_identity_attributes({
            "dob": "1980-01-01",
            "region": "SG",
            "company": "Acme Pte Ltd",
            "num_results": 10,          # noise, not identity
            "recency_days": 365,        # noise, not identity
        })
        assert "dob" in attributes
        assert "num_results" not in attributes

    def test_ignores_empty_values(self):
        attributes = extract_identity_attributes({"dob": "", "region": None})
        assert not attributes


class TestContentFingerprint:
    def test_identical_text_fingerprints_identically(self):
        assert content_fingerprint("hello world") == content_fingerprint("hello world")

    def test_different_text_fingerprints_differently(self):
        a = content_fingerprint("X was charged with fraud.")
        b = content_fingerprint("Charges against X were dropped.")
        assert a != b

    def test_empty_and_none_produce_no_fingerprint(self):
        # A missing fingerprint must be falsy so callers can distinguish
        # "unchanged" from "could not be read".
        assert not content_fingerprint(None)
        assert not content_fingerprint("")

    def test_whitespace_only_changes_are_ignored(self):
        # Re-flowed HTML is not an editorial change, and must not trigger a
        # spurious re-analysis of every article on every run.
        assert content_fingerprint("hello   world") == content_fingerprint("hello world")
