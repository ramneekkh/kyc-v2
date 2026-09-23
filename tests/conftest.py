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

"""Shared pytest configuration.

These tests deliberately exercise pure decision logic only -- no Spanner, no
Pub/Sub, no Gemini, no network. The functions under test are the ones that
determine a compliance outcome, and those must be verifiable without
credentials, in CI, in under a second.
"""

import os
import sys

# Import the package from the repo root regardless of where pytest is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Keep identity derivation deterministic across machines.
os.environ.setdefault("KYC_TENANT_ID", "test-tenant")
