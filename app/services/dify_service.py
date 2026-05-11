import requests
import os
import time
import mimetypes

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

# Chatbot app – used to start and continue conversations after extraction.
DIFY_CHAT_API_KEY = os.getenv("DIFY_CHAT_KEY", "")

# ── File upload helper ────────────────────────────────────────────────────────

def upload_file_to_dify(file_path: str, user: str = "system", api_key: str = "") -> str:
    """
    Upload a local file to Dify and return the Dify file_id.
    Used before calling any workflow that needs file inputs.
    """
    key = api_key or DIFY_API_KEY
    upload_url = f"{DIFY_URL}/v1/files/upload"
    mime_type, _ = mimetypes.guess_type(file_path)
    mime_type = mime_type or "application/octet-stream"
    with open(file_path, "rb") as f:
        response = requests.post(
            upload_url,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (os.path.basename(file_path), f, mime_type)},
            data={"user": user},
            timeout=120,  # 2 minutes for file upload
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
) -> dict:
    """
    Call the Dify 'Scoring' workflow for a single tenderer file.
    Returns the raw outputs dict from the workflow, which includes
    `overall_summary_json` (a stringified JSON string).
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
        timeout=600,
    )
    response.raise_for_status()
    result = response.json()
    outputs = result.get("data", {}).get("outputs", result)
    return outputs


def score_tenderer_bytes(
    file_bytes: bytes,
    file_name: str,
    marking_scheme: dict,
    user: str = "system",
) -> dict:
    """
    Score a tenderer file given raw bytes (for background-task usage where
    the file may not be on disk).  Saves to a temp file, scores, then cleans up.
    Returns the raw workflow outputs dict.
    """
    import tempfile, os as _os
    suffix = _os.path.splitext(file_name)[1] or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        return score_tenderer_submission(tmp_path, marking_scheme, user=user)
    finally:
        _os.unlink(tmp_path)


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

def initiate_chat_with_document(file_path: str, user: str = "system") -> dict:
    """
    Two-step flow:
      1. Run the Scheme Analysis workflow → returns `marking_scheme` JSON.
      2. Call /v1/chat-messages with the marking scheme as context to open
         a new conversation the user can continue.

    Returns a dict with keys: conversation_id, message_id, answer.
    """
    # ── Step 1: extract marking scheme via workflow ───────────────────────────
    file_id = upload_file_to_dify(file_path, user=user, api_key=DIFY_SCHEME_ANALYSIS_API_KEY)

    workflow_response = requests.post(
        DIFY_SCHEME_ANALYSIS_WORKFLOW_URL,
        headers={
            "Authorization": f"Bearer {DIFY_SCHEME_ANALYSIS_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "inputs": {
                "tender_doc": {
                    "transfer_method": "local_file",
                    "upload_file_id": file_id,
                    "type": "document",
                },
                "username": user,
            },
            "response_mode": "blocking",
            "user": user,
        },
        timeout=600,
    )
    workflow_response.raise_for_status()
    outputs = workflow_response.json().get("data", {}).get("outputs", {})
    marking_scheme = outputs.get("marking_scheme", {})

    # ── Step 2: open a chat conversation with the marking scheme as context ───
    import json as _json
    opening_query = (
        f"I have uploaded a tender document. "
        f"Here is the extracted marking scheme:\n\n"
        f"```json\n{_json.dumps(marking_scheme, indent=2)}\n```\n\n"
        f"Please review this marking scheme and let me know if you would like "
        f"to adjust any criteria, weights, or requirements before we proceed."
    )

    chat_response = requests.post(
        DIFY_CHAT_URL,
        headers={
            "Authorization": f"Bearer {DIFY_CHAT_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "inputs": {"UserName": user},
            "query": opening_query,
            "response_mode": "blocking",
            "conversation_id": "",
            "user": user,
            "files": [],
        },
        timeout=600,
    )
    chat_response.raise_for_status()
    result = chat_response.json()
    return {
        "conversation_id": result.get("conversation_id", ""),
        "message_id": result.get("id") or result.get("message_id", ""),
        "answer": result.get("answer", ""),
        "marking_scheme": marking_scheme,
    }


def upload_and_process_requirement(project_id, file_path):
    print(f"Background task finished for Project {project_id}")