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

"""
This file contains the core business logic and data definitions for the 
KYC (Know Your Customer) due diligence search functionality. It centralizes 
the definitions of adverse media and risk categories.
"""

# --- Framework of Adverse Media & KYC Risk Categories ---
# This structure defines the key areas of risk to investigate for an individual.
KYC_RISK_CATEGORIES = {
    "Financial Crime": {
        "description": "Risks related to illegal financial activities.",
        "keywords": ["money laundering", "launder*", "terrorist financing", "fraud", "corruption", "corrupt*", "bribery", "brib*", "embezzlement", "tax evasion", "tax amnesty", "scam*"]
    },
    "Regulatory & Legal": {
        "description": "Risks related to sanctions, legal actions, and regulatory breaches.",
        "keywords": ["illegal", "arrest*", "charged", "convict*", "sentenced", "indictment", "investigat*", "lawsuit", "litigation", "misdemeanor*", "offen*", "prosecut*", "alleg*"]
    },
    "Sanctions & Geopolitical Risk": {
        "description": "Risks related to connections with sanctioned entities, countries, or high-risk jurisdictions.",
        "keywords": ["sanctions", "sanctions evasion", "DPRK", "North Korea", "Iran", "Cuba", "Syria", "Crimea", "Donetsk", "Luhansk", "Kherson", "Zaporizhzhia", "Russia", "Venezuela", "Burma", "Myanmar", "Belarus"]
    },
    "Reputational & Ethical": {
        "description": "Risks related to unethical behavior and negative public perception.",
        "keywords": ["scandal", "controversy", "misconduct", "trafficking", "traffick*", "insider dealing", "insider deal", "unethical", "allegations"]
    },
    "Politically Exposed Person (PEP) Status": {
        "description": "Risks associated with individuals who hold or have held a public function.",
        "keywords": ["politically exposed person", "government official", "minister", "senator", "political party", "state-owned enterprise"]
    }
}

def get_risk_category_options():
    """Returns a flat list of risk category names for potential use in the UI."""
    return list(KYC_RISK_CATEGORIES.keys())

