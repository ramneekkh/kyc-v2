# AI-Powered KYC Adverse Media Screening Agent

An advanced due diligence tool that automates the screening of individuals and entities against adverse media using **Google Search** and **Google Gemini**.

## 🚀 Overview
This application empowers compliance officers and risk analysts to:
-   **Automate Screening**: specific individuals or companies are screened against thousands of web sources in real-time.
-   **Intelligent Analysis**: Uses **Gemini 1.5** to read, analyze, and score each search result for risk relevance.
-   **Entity Graphing**: Automatically extracts and visualizes relationships (people, companies, events) from the news coverage.
-   **Executive Summaries**: specific findings are synthesized into a cohesive risk report with a "High/Medium/Low" recommendation.

## 🏗 System Architecture

For a detailed breakdown of modules and dataflow, please refer to the **[Architecture Documentation](architecture.md)**.

### Technology Stack
-   **Frontend**: React (Vite), Tailwind CSS, Lucide React icons.
-   **Backend**: Python Flask, Gunicorn.
-   **AI/ML**: Google Vertex AI (Gemini 1.5 Pro/Flash).
-   **Search**: Google Custom Search JSON API.
-   **Database**: Google Cloud Spanner (PostgreSQL Dialect).
-   **Storage**: Google Cloud Storage (for large text content).

## 📂 Project Structure

```bash
/
├── backend/
│   ├── app.py                 # API Gateway & SSE Streaming
│   ├── search/                # Core Logic
│   │   ├── core.py            # Main Orchestrator
│   │   ├── utils.py           # Scraping, Analysis, Summary Gen
│   │   └── prompts.py         # Gemini Prompt Templates
│   ├── database/              # Integration Layer
│   │   ├── spanner_client.py  # Spanner CRUD & GCS Offloading
│   │   └── schema_manager.py  # Database Migrations
│   └── storage/
│       └── gcs_client.py      # GCS Blob Uploads
├── frontend/
│   ├── src/
│   │   ├── App.jsx            # Main UI & State
│   │   └── GraphView.jsx      # Entity Graph Visualization
│   └── dist/                  # Production Build Artifacts
└── run_backend.sh             # Startup Script
```

## 🛠 Local Development Setup

### Prerequisites
-   **Python 3.9+**
-   **Node.js 18+**
-   **Google Cloud Platform** Project with:
    -   Vertex AI API enabled.
    -   Custom Search API enabled (and a CSE ID).
    -   Cloud Spanner API enabled.
    -   Cloud Storage API enabled.

### 1. Environment Variables
Create a `.env` file in `backend/` (or root) with the following:

```ini
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_API_KEY=your-api-key
GOOGLE_CSE_ID=your-cse-id
SPANNER_INSTANCE=your-instance-id
SPANNER_DATABASE=your-database-id
GCS_BUCKET_NAME=your-bucket-name
```

### 2. Backend Setup
The backend runs on Flask/Gunicorn. Use the provided helper script:

```bash
# Install dependencies and start server
bash run_backend.sh
```
*   Server runs on `http://localhost:8080`.
*   API docs/endpoints are at `/api/kyc-check`, `/api/subjects`.

### 3. Frontend Setup
The frontend is a standard Vite React app.

```bash
cd frontend
npm install
npm run dev
```

To build for production (served by Flask):
```bash
npm run build
```

## ✨ Key Features

1.  **Incremental Search**: Stream results as they are found. No waiting for the full job to finish.
2.  **Map-Reduce Summarization**: Handles massive search contexts by splitting findings into chunks, summarizing them in parallel with Gemini, and consolidating the result.
3.  **Graph Visualization**: Interactive node-link diagram showing the subject's network.
4.  **Evidence Offloading**: Large article text is automatically offloaded to GCS to optimize database performance, while metadata remains in Spanner.
5.  **Robust Error Handling**: Automatic retries for quota limits (429s) and connection issues.

## 📚 API Specification

### Base URL
`http://localhost:8080`

---

### 1. Search & Analysis

#### 1.1. Stream KYC Check (SSE)
Initiates a real-time KYC check and streams progress events.

- **Endpoint**: `GET /api/kyc-check`
- **Type**: Server-Sent Events (SSE)

**Query Parameters:**
| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `subjectName` | string | Yes | Name of the subject to research. |
| `company` | string | No | Associated company. |
| `profession` | string | No | Profession/Job title. |
| `region` | string | No | Geographic region of interest. |
| `age` | string | No | Approximate age of the subject. |
| `dob` | string | No | Date of birth. |
| `ownership` | string | No | Ownership details. |
| `spouse` | string | No | Spouse's name. |
| `alias` | string | No | Known aliases. |
| `customKeywords`| string | No | Comma-separated list of additional keywords. |
| `incremental` | string | No | `true` or `false`. If `true`, only searches for recent data. |

**Response Stream Events:**
- `status`: `{ status: "string", message: "string", data: any }`
- `usage_update`: `{ data: { input_tokens, output_tokens, cost } }`
- `kyc_result_generated`: `{ message: "Analyzed a source.", data: FindingObject }`
- `kyc_summary_generated`: `{ message: "Overall assessment complete.", data: SummaryObject }`

---

#### 1.2. Submit Batch Search
Submits multiple subjects for background processing.

- **Endpoint**: `POST /api/search/submit`
- **Content-Type**: `application/json`

**Request Body:**
```json
{
  "subjects": [
    {
      "name": "John Doe",
      "company": "Tech Corp",
      "mode": "fresh" // "fresh" | "incremental" | "default"
    }
  ],
  "mode": "fresh" // Global mode override
}
```

**Response:**
```json
{
  "batch_id": "uuid",
  "queued": [
    {
      "name": "John Doe",
      "subject_id": "uuid-derived-from-name"
    }
  ],
  "skipped_existing": []
}
```

---

#### 1.3. Get Processing Result
Retrieves the full analysis result for a batch-processed subject.

- **Endpoint**: `GET /api/results/<subject_id>`

**Response:**
- **200 OK**: JSON object containing `findings`, `graph_data`, `summary`, and `metadata`.
- **404 Not Found**: If the result is not yet available or does not exist.

---

### 2. Subjects & Findings

#### 2.1. List Subjects
Returns a summary list of all analyzed subjects.

- **Endpoint**: `GET /api/subjects`

**Response:**
```json
[
  {
    "subject_id": "uuid",
    "name": "John Doe",
    "status": "ACTIVE",
    "created_at": "ISO8601"
  }
]
```

#### 2.2. Get Subject Details
Returns full details, findings, and graph data for a specific subject.

- **Endpoint**: `GET /api/subjects/<subject_id>`

**Response:**
```json
{
  "subject": { ... },
  "findings": [ ... ],
  "graph_data": { "nodes": [], "edges": [] }
}
```

#### 2.3. Check Subject Existence
Checks if a subject exists by name.

- **Endpoint**: `GET /api/subjects/check`
- **Query Parameter**: `name`

**Response:**
```json
{
  "exists": true,
  "subject": { "subject_id": "..." }
}
```

---

### 3. Utilities

#### 3.1. Generate Graph
Generates a knowledge graph from a provided list of findings using Gemini.

- **Endpoint**: `POST /api/generate-graph`

**Request Body:**
```json
{
  "subjectName": "John Doe",
  "findings": [ { "id": "...", "snippet": "..." } ]
}
```

**Response:**
```json
{
  "nodes": [ ... ],
  "edges": [ ... ]
}
```

#### 3.2. Export Findings
Prepares findings for export (enriched with scraped content).

- **Endpoint**: `POST /api/export-findings`

**Request Body:**
```json
{
  "subjectName": "John Doe",
  "results": [ ... ]
}
```

**Response:**
```json
{
  "findings": [ ... ], // Enriched findings
  "graph_html": "<html string>",
  "graph_data": { ... }
}
```

#### 3.3. Get Config
Returns frontend configuration and dropdown options.

- **Endpoint**: `GET /api/config`

**Response:**
```json
{
  "SEARCH_TOOLTIPS": { ... },
  "RISK_CATEGORY_OPTIONS": [ ... ]
}
```

## 📄 License
Copyright 2025 Google LLC. Licensed under the Apache License, Version 2.0.# kyc-v2
