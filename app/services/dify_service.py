import requests
import os
import time

DIFY_API_KEY = os.getenv("DIFY_API_KEY", "app-UnhDDkWMmnpIj70EcEVfkomo")
DIFY_URL = os.getenv("DIFY_BASE_URL", "http://localhost:80")

# ── Existing chat / draft endpoints ───────────────────────────────────────────

DIFY_CHAT_URL = f"{DIFY_URL}/v1/chat-messages"

# ── NEW: Dify agent endpoints (configure these agents inside your Dify instance)
# Agent 1 – Analyses an uploaded tender document and returns a marking scheme JSON.
DIFY_SCHEME_ANALYSIS_WORKFLOW_URL = f"{DIFY_URL}/v1/workflows/run"
DIFY_SCHEME_ANALYSIS_API_KEY = os.getenv("DIFY_SCHEME_ANALYSIS_KEY", "")

# Agent 2 – Scores a tenderer's submission against a given marking scheme JSON.
DIFY_SCORING_WORKFLOW_URL = f"{DIFY_URL}/v1/workflows/run"
DIFY_SCORING_API_KEY = os.getenv("DIFY_SCORING_KEY", "")

# ── File upload helper ────────────────────────────────────────────────────────

def upload_file_to_dify(file_path: str, user: str = "system", api_key: str = "") -> str:
    """
    Upload a local file to Dify and return the Dify file_id.
    Used before calling any workflow that needs file inputs.
    """
    key = api_key or DIFY_API_KEY
    upload_url = f"{DIFY_URL}/v1/files/upload"
    with open(file_path, "rb") as f:
        response = requests.post(
            upload_url,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (os.path.basename(file_path), f)},
            data={"user": user},
        )
    response.raise_for_status()
    return response.json()["id"]


# ── Scheme Analysis Agent ─────────────────────────────────────────────────────

def analyse_marking_scheme(file_path: str, user: str = "system") -> dict:
    """
    Call the Dify 'Scheme Analysis' workflow agent.
    Expects the agent to return JSON with shape:
    {
      "marking_scheme": {
        "<criterion_label>": { "description": "...", "max_score": 10 },
        ...
      }
    }
    Returns the parsed dict on success, raises on failure.
    """
    file_id = upload_file_to_dify(file_path, user=user, api_key=DIFY_SCHEME_ANALYSIS_API_KEY)

    payload = {
        "inputs": {
            "tender_document": {
                "transfer_method": "local_file",
                "upload_file_id": file_id,
                "type": "document",
            }
        },
        "response_mode": "blocking",
        "user": user,
    }

    response = requests.post(
        DIFY_SCHEME_ANALYSIS_WORKFLOW_URL,
        headers={
            "Authorization": f"Bearer {DIFY_SCHEME_ANALYSIS_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    result = response.json()
    # Dify workflow output lives under data.outputs
    return result.get("data", {}).get("outputs", result)


# ── Scoring Agent ─────────────────────────────────────────────────────────────

def score_tenderer_submission(
    tenderer_file_path: str,
    marking_scheme: dict,
    user: str = "system",
) -> list[dict]:
    """
    Call the Dify 'Scoring' workflow agent for a single tenderer file.
    Expects the agent to return JSON with shape:
    {
      "results": [
        {
          "criterion": "...",
          "score": 8,
          "max_score": 10,
          "status": "pass",           // "pass" | "fail" | "dq"
          "is_disqualified": false,
          "dq_reason": null,
          "evidence": "...",
          "comment": "..."
        },
        ...
      ]
    }
    Returns the list of result dicts.
    """
    file_id = upload_file_to_dify(tenderer_file_path, user=user, api_key=DIFY_SCORING_API_KEY)

    payload = {
        "inputs": {
            "tenderer_document": {
                "transfer_method": "local_file",
                "upload_file_id": file_id,
                "type": "document",
            },
            "marking_scheme": marking_scheme,
        },
        "response_mode": "blocking",
        "user": user,
    }

    response = requests.post(
        DIFY_SCORING_WORKFLOW_URL,
        headers={
            "Authorization": f"Bearer {DIFY_SCORING_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    result = response.json()
    outputs = result.get("data", {}).get("outputs", result)
    return outputs.get("results", [])


# ── Legacy helpers (kept for backward compatibility) ──────────────────────────

def get_ai_draft(company: str, title: str, amount: float):
    headers = {"Authorization": f"Bearer {DIFY_API_KEY}", "Content-Type": "application/json"}
    prompt_input = (
        f"Generate a professional tender proposal for {company}. "
        f"The project title is '{title}' and the proposed budget is ${amount}. "
        f"Please include sections for: Executive Summary, Proposed Solution, and Cost Analysis."
    )
    response = requests.post(
        DIFY_CHAT_URL,
        headers=headers,
        json={
            "inputs": {},
            "query": prompt_input,
            "response_mode": "blocking",
            "conversation_id": "",
            "user": "abc-123",
        },
    )
    response.raise_for_status()
    return response.json().get("answer", "")

def upload_and_process_requirement(project_id, file_path):
    print(f"Background task finished for Project {project_id}")