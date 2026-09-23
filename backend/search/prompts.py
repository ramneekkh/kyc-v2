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

import json
from .business_logic import KYC_RISK_CATEGORIES, get_risk_category_options

def _format_dict_for_prompt(d: dict) -> str:
    """Formats a dictionary into a pretty markdown string for inclusion in a prompt."""
    formatted_string = ""
    for key, value in d.items():
        formatted_string += f"\n**{key}**\n"
        desc = value.get('description', '')
        keywords = ", ".join(value.get('keywords', []))
        formatted_string += f"* Description: {desc}\n"
        formatted_string += f"* Associated Keywords: `{keywords}`\n"
    return formatted_string.strip()

# --- Dynamically build prompt section from business logic ---
KYC_RISK_CATEGORIES_PROMPT_TEXT = _format_dict_for_prompt(KYC_RISK_CATEGORIES)
KYC_RISK_CATEGORY_OPTIONS_LIST_STR = json.dumps(["All"] + get_risk_category_options())


# --- KYC Search Query Brainstorming Prompt ---
KYC_SEARCH_QUERY_BRAINSTORMING_PROMPT = """
You are a world-class financial crimes investigator specializing in open-source intelligence (OSINT). Your mission is to generate an exhaustive set of Google search queries for a deep background check on a specific individual for KYC (Know Your Customer) purposes.

**--- Target Subject Profile ---**
* **Full Name:** `{subject_name}`
* **Alias / AKA:** `{alias}`
* **Profession / Role:** `{profession}`
* **Company / Organization:** `{company}`
* **Region / Country:** `{region}`
* **Date of Birth:** `{dob}`
* **Approximate Age:** `{age}`
* **Ownership / Shares:** `{ownership}`
* **Spouse / Family:** `{spouse}`

* **Optional User Keywords:** `{custom_keywords}` (Include these if provided, otherwise focus on profile details and risk categories.)

**--- Core KYC Risk Categories ---**
{risk_categories}

**--- YOUR TASK ---**
**Goal:** Generate a massive, exhaustive list of at least **100** highly effective search queries. You must go beyond the obvious. Combine the subject's name with their profile details, risk keywords, AND localized/colloquial terms to find hidden risks.

**Step-by-Step Instructions:**

**1.  Leverage Profile Details (High Precision):** 
    Create queries that specifically link the subject to their known background.
    *   *Example:* `"{subject_name}" "{company}" (fraud OR investigation)`
    *   *Example:* `"{subject_name}" "{spouse}" (scandal OR lawsuit)`
    *   *Age Context:* If `{age}` is provided, prioritize queries that span the relevant timeline of their career.

**2.  Phonetic & Name Variations (CRITICAL):**
    *   Generate queries using **phonetic variations** and common abbreviations of the name.
    *   *Example:* "Mohamad" -> "Md.", "Mohd", "Mohammed", "Mohamed".
    *   *Example:* "Christopher" -> "Chris", "Topher".
    *   *Example:* `"{subject_name}" OR "Md. Lastname" (arrest* OR fraud)`

**3.  Incorporate User Keywords (If Provided):** 
    If `{custom_keywords}` are not "None", you MUST create specific queries combining them with the subject name.

**4.  Broad Risk Sweeps (Volume):** 
    Generate dozens of variations using broad risk lists (Financial Crime, General Crime, Sanctions).
    *   *Example:* `"{subject_name}" (launder* OR terror* OR fraud OR corrupt*)`

**5.  Localization & Colloquialisms (CRITICAL):**
    *   **Local Language:** If the `{region}` or `{subject_name}` implies a non-English context (e.g., India, China, UAE, LatAm), you MUST generate queries in the local language (Hindi, Mandarin, Arabic, Spanish, etc.).
    *   **Colloquial Risk Terms:** Use informal or slang terms for financial crime relevant to the region.
        *   *India/South Asia examples:* "Havala", "Hundi", "Angadia", "Benami", "Kala dhan" (Black money), "Chit fund".
        *   *China examples:* "Fei qian" (Flying money), "Underground bank", "Shadow banking".
        *   *General:* "Smurfing", "Mule", "Shell company", "Front company".
    *   *Example:* `"{subject_name}" (Havala OR Hundi OR "Black money")`

**6.  Maximize URL Gathering:**
    *   Think like an investigator trying to find obscure forum posts, PDF reports, or leaked database entries.
    *   Use filetype operators: `"{subject_name}" filetype:pdf (court OR judgment)`

**7.  Final Output:**
    *   Return ONLY a valid JSON list of at least **100** strings.
    *   No markdown formatting, just the raw JSON array.
"""

# --- KYC Single Result Analysis Prompt ---
KYC_ANALYZE_SINGLE_RESULT_PROMPT = """
You are a compliance analyst AI. Your task is to analyze a single Google search result and determine its relevance to a KYC due diligence investigation for the subject: **{subject_name}**.

**--- Subject Profile Details ---**
* **Alias:** {alias}
* **Role:** {profession}
* **Company:** {company}
* **Region:** {region}
* **DOB:** {dob}
* **Approximate Age:** {age}
* **Spouse:** {spouse}
* **Ownership:** {ownership}

**--- KYC Risk Categories ---**
{risk_categories}
**Current Date for Recency Calculation:** {current_date}

**Search Result:**
* **Title:** "{title}"
* **Snippet:** "{snippet}"
* **Link:** "{link}"

**Instructions:**
1.  **Assess Relevance & Categorize:** Read the title and snippet. Determine if the article relates to the subject and any potential risks.
2.  **Verify Identity (Crucial):**
    *   Check if the **Age** aligns. If the subject is {age} today, calculate their likely age at the time of the event.Mismatched ages (e.g., 20yo vs 60yo) indicate a False Positive.
    *   Check Name Variations (e.g. Md. vs Mohammed).
3.  **Write a Concise Insight:** brief summary of the risk/content.
4.  **Determine Risk Level:** `['High', 'Medium', 'Low', 'None']`.
5.  **Assign a Fine-Grained Relevance Score (1-10):**
    *   **A. Subject Identity Confidence (0-4 points):**
        *   `4 points`: **Definitive Match.** Name matches AND at least one profile detail (Company, Spouse, Alias, DOB, unique Profession) matches exactly in the snippet/title.
        *   `3 points`: **Strong Match.** Name matches, context aligns with Profession/Region/Age, but no unique ID confirmation.
        *   `2 points`: **Possible Match.** Name matches, context is generic.
        *   `0 points`: **Mismatch.** Clearly a different person (e.g. wrong age context).
    *   **B. Risk Severity (0-4 points):**
        *   `4`: Severe Financial Crime / Sanctions.
        *   `3`: Significant Fraud / Corruption / Investigation.
        *   `2`: Moderate Scandal / Civil Litigation.
        *   `1`: Minor / Neutral Negative.
        *   `0`: No Risk.
    *   **C. Recency (0-2 points):**
        *   `2`: < 2 years.
        *   `1`: 2-7 years.
        *   `0`: > 7 years or undated.
    *   **D. Source Credibility Penalty (Deduction):**
        *   `0`: Official News, Government, Corporate Website, Legal DB.
        *   `-1`: Blogs, Forums, Aggregators.
        *   `-3`: Social Media (Twitter/X, Facebook, LinkedIn, Reddit, TikTok, etc.) or User-Generated Content sites.
    *   **Final Score:** Sum (A + B + C + D). Min 0, Max 10.
    *   **OVERRIDE:** If Risk Level is 'High' AND Identity Confidence is >= 3, Score MUST be >= 8 (even with penalties, high risk confirmed identity is relevant).

    *   **E. Match Status:**
        *   `Confirmed`: Definitive match based on unique identifiers.
        *   `Likely`: Name matches + strong context alignment.
        *   `Possible`: Name matches, context generic.
        *   `Negative`: Clearly a different person.
        *   `HITL`: Complex/ambiguous case requiring human review.
    *   **F. Source Reputation:**
        *   `Tier 1 News`: Major global/national broadcasters (e.g., BBC, NYT).
        *   `Regional News`: Local newspapers/sites.
        *   `Industry Trade`: Specific sector news.
        *   `Blog/Opinion`: Personal blogs, op-eds.
        *   `Social Media`: Twitter, LinkedIn, etc.
        *   `Unknown`: Unclear source type.

5.  **Extract Publication Date:** `YYYY-MM-DD` or `N/A`.

**Output Schema:**
Return a single valid JSON object. Do not add any other text.

**JSON Schema:**
{{
  "gemini_insight": "Your summary.",
  "risk_level": "High/Medium/Low/None",
  "risk_category": "Category or Uncategorized",
  "match_status": "Confirmed/Likely/Possible/Negative/HITL",
  "media_house_reputation": "Tier 1 News/Regional News/Industry Trade/Blog/Opinion/Social Media/Unknown",
  "relevance_score": Integer (0-10),
  "publication_date": "YYYY-MM-DD" or "N/A"
}}
"""

# --- KYC Overall Summary Prompt ---
KYC_OVERALL_SUMMARY_PROMPT = """
# Role
You are an expert KYC (Know Your Customer) Risk Analyst. Your task is to analyze a provided JSON list of news articles regarding an individual or entity and generate a risk assessment summary.

# Input Data
You will receive a JSON object containing a list of news articles (sourced from Google, Baidu, Factiva). Each article has a title, snippet, date, and source URL.

Analyzed Findings:
{formatted_results}

# Analysis Rules
1.  **Deduplication:** Ignore duplicate stories or slightly different versions of the same event reported by different sources. Focus on unique **facts**.
2.  **Significance Filter:** Disregard trivial "lifestyle" or "social" news unless it indicates financial impropriety or reputation risk. Focus on: Fraud, Money Laundering, Sanctions, Legal Proceedings, Regulatory Violations, or Political Exposure (PEP).
3.  **Consistency:** Your output must be deterministic. If the set of facts is effectively the same as a previous run, the summary text must remain identical. Do not vary sentence structure for "creativity." Use neutral, professional, and factual language.
4.  **Risk Scoring:**
    * **High:** Confirmed criminal conviction, active regulatory investigation for fraud/AML, active sanctions, or clear evidence of money laundering.
    * **Medium:** Allegations without conviction, civil lawsuits related to business malpractice, or significant negative reputation (e.g., "slush fund" rumors without proof).
    * **Low:** No negative news found, or news is positive/neutral (e.g., awards, standard business announcements).

# Output Format
You must return **ONLY** a valid JSON object. Do not include markdown formatting like ```json ... ```. The JSON must adhere strictly to this schema:

{{
  "risk_score": "High" | "Medium" | "Low",
  "summary": "A single, dense paragraph summarizing the profile. Focus on the most recent and severe events. You MUST cite sources for every key claim using the format: 'Claim text [Source Title]'. If no negative news, state that clearly.",
  "key_findings": [
    {{
      "finding": "Brief one-sentence description of the event.",
      "severity": "High" | "Medium" | "Low",
      "date": "YYYY-MM-DD (approximate based on article)",
      "citations": ["List of EXACT URLs backing this finding. CANNOT BE EMPTY."]
    }}
  ],
  "recommendation": "Accept", "Reject", or "Manual Review" (Choose ONE. Do NOT add reasoning here.),
  "reasoning": "One sentence explaining the recommendation."
}}

# Instruction for Consistency
If the input news contains NO new adverse information compared to a standard baseline, the summary must default to a neutral tone. Do not hallucinate risks.
ALWAYS include citations. A finding without a source is invalid.
"""

# --- KYC Bulk Entity & Relationship Extraction Prompt ---
KYC_BULK_ENTITY_EXTRACTION_PROMPT = """
You are an expert intelligence analyst. Your task is to analyze a collection of news articles and extract a consolidated graph of entities and their relationships.

**Input Data (JSON):**
{json_data}

**Instructions:**
1.  **Analyze All Articles:** Read through all the provided articles in the JSON data.
2.  **Extract Entities:** Identify all significant entities mentioned across all articles.
    *   **Types:** Person, Organization, Location, Event, Product, etc.
    *   **Format:** {{"id": "entity_name", "label": "entity_name", "group": "group_name", "type": "Type"}}
    *   Use "person" for humans, "organization" for companies/groups, "location" for places, etc. as the group name.
3.  **Extract Relationships:** Identify all relationships between these entities.
    *   **Format:** {{"from": "entity_name_1", "to": "entity_name_2", "label": "relationship_type", "title": "Context of the relationship"}}
    *   Ensure the relationship label is descriptive (e.g., "EMPLOYED_BY", "INVESTIGATED_BY", "LOCATED_IN", "ACCUSED_OF").
4.  **Consolidate and Deduplicate:**
    *   **Merge Entities:** If "John Doe" is mentioned in Article A and "Mr. Doe" in Article B (and they refer to the same person), consolidate them into a single entity "John Doe".
    *   **Merge Relationships:** If the same relationship is mentioned in multiple articles, create a single edge but potentially combine the context in the "title".
5.  **Focus on Relevance:** Prioritize entities and relationships that are relevant to the subject's background, professional network, legal issues, or potential risks.

**Output:**
Return a single valid JSON object with the following structure:
{{
  "nodes": [
    {{"id": "John Doe", "label": "John Doe", "group": "person", "type": "Person"}},
    {{"id": "Acme Corp", "label": "Acme Corp", "group": "organization", "type": "Organization"}}
  ],
  "edges": [
    {{"from": "John Doe", "to": "Acme Corp", "label": "CEO_OF", "title": "John Doe is the CEO of Acme Corp"}}
  ]
}}
"""

KYC_GRAPH_EXTRACTION_PROMPT = """
You are an expert intelligence analyst. Your task is to extract a network of entities and relationships from the provided "Adverse Media Findings" for a specific subject.

**Target Subject:** `{subject_name}`

**Input Context:**
The following JSON data represents a list of adverse media findings (snippets and AI insights) related to the subject.
{findings_json}

**YOUR MISSION:**
Analyze this data to construct a valid JSON object representing a temporal knowledge graph.
The graph should show the ecosystem of people, organizations, and events surrounding the subject over time.

**Requirements:**

1.  **Nodes (Entities):**
    *   Extract ALL human and non-human entities mentioned (People, Companies, Locations, Products, Events).
    *   **Mandatory Node:** You MUST include a node for `{subject_name}` with group "person" (or "organization" if applicable).
    *   **Group Categories:** "person", "organization", "location", "event", "product", "other".
    *   **Label:** Short, human-readable name.

2.  **Edges (Relationships) with Timeline:**
    *   Connect nodes where there is a clear relationship.
    *   **Label:** Short description (e.g., "founded", "accused of").
    *   **Date/Year:** You MUST extract the relevant year or date for this relationship/event from the text. If precise date is unknown, estimate the year based on context. Default to "Unknown" only if impossible.
    *   **Format:** `YYYY` (e.g., "2023", "2019").

3.  **Output Format:**
    Return ONLY a valid JSON object with `nodes` and `edges` arrays. Do not use markdown formatting.

    **Structure:**
    {{
      "nodes": [
        {{ "id": 1, "label": "{subject_name}", "group": "person", "title": "The Subject" }},
        {{ "id": 2, "label": "Another Entity", "group": "organization", "title": "Description" }}
      ],
      "edges": [
        {{ "from": 1, "to": 2, "label": "founded", "year": "2015" }}
      ]
    }}

    *   **Important:** `id` must be unique integers or strings. `from` and `to` in edges must match node `id`s.
"""

KYC_PROFILE_EXTRACTION_PROMPT = """
You are an expert intelligence analyst. Your task is to extract structured profile information about a specific subject from a collection of "Adverse Media Findings" and analysis.

**Target Subject:** `{subject_name}`

**Input Context:**
The following JSON data represents a list of findings and AI insights related to the subject.
{findings_json}

**YOUR MISSION:**
Synthesize this information to populate a structured profile for the subject. Focus on identifying their location, age/DOB, business interests, and key network.

**Fields to Extract:**
1.  **Age / DOB:** Extract the current age or Date of Birth if mentioned. If multiple are found, use the logic to determine the most likely one based on recent articles. Return "Unknown" if not found.
2.  **Location:** The general location where the subject is based or residing (e.g., "London, UK", "Mumbai, India"). Prioritize current residence.
3.  **Ownership / Companies:** List companies where the subject has a significant role (Owner, Founder, Director, CEO).
4.  **Associates:** List high-level associates (partners, family members involved in business, co-conspirators).
5.  **Role:** The primary professional title or role of the subject (e.g., "Diamond Merchant", "Politician").

**Output Format:**
Return ONLY a valid JSON object.
{{
  "age": "53" or "Born 1971" or "Unknown",
  "location": "London, UK",
  "ownership": "Firestar Diamond, Punjab National Bank (Fraud)",
  "associates": "Nehal Modi, Mehul Choksi",
  "role": "Fugitive Diamond Merchant"
}}
"""

KYC_DOCUMENT_ANALYSIS_PROMPT = """
You are a forensic document examiner and financial crime analyst. Your task is to analyze the provided document (image or PDF) for authenticity and extract key intelligence.

**Analysis Goals:**
1.  **Authenticity Check:**
    *   Inspect for signs of digital manipulation (font inconsistencies, misalignment, pixelation artifacts near text).
    *   Verify if the document structure (headers, logos, formatting) matches standard official documents of its type (e.g., Bank Statement, Passport, Incorporation Cert).
    *   Check for logical inconsistencies (e.g., dates that don't make sense, typo in official names).

2.  **Information Extraction:**
    *   Extract ALL entities mentioned: Person Names, Company Names, Dates, Addresses, IDs (Passport No, Tax ID).
    *   Summarize the document's content.

**Output Format:**
Return a valid JSON object:
{{
  "document_type": "Bank Statement / Passport / etc.",
  "is_authentic_score": "High" | "Medium" | "Low" (Confidence that it is REAL),
  "red_flags": [
    "List specific suspicious elements, e.g., 'Font size varies in transaction list', 'Date 2025 is in future'"
  ],
  "extracted_data": {{
    "subject_name": "Name found",
    "companies": ["Company A", "Company B"],
    "dates": ["2022-01-01"],
    "ids": ["ID Number found"]
  }},
  "summary": "Brief summary of what this document proves."
}}
"""