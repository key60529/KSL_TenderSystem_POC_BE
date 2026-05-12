"""
Reviews router — handles:
  1. Analysing an uploaded tender document to produce a marking scheme.
  2. Scoring multiple tenderer submissions against a saved project's marking scheme.
  3. Async queue-based scoring with per-file polling.
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
from .. import models, database, auth
from ..services import dify_service
import os, shutil, json, threading, base64

router = APIRouter(prefix="/reviews", tags=["Reviews"])

UPLOAD_DIR = "uploaded_tenders"


# ── Helper ────────────────────────────────────────────────────────────────────

def _save_upload(file: UploadFile, prefix: str = "") -> str:
    """Save an uploaded file to disk and return the local path."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_name = f"{prefix}{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, file_name)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return file_path


# ── Step 1-2: Analyse tender document → marking scheme ────────────────────────

@router.post("/analyse-scheme")
async def analyse_scheme(
    file: UploadFile = File(..., description="Tender document (PDF or DOCX)"),
    current_user: models.UserTable = Depends(auth.get_current_user),
):
    """
    Upload a tender document. The Dify Scheme Analysis agent reads it and
    returns a structured marking scheme JSON for the user to review.

    The scheme is NOT saved yet — the frontend presents it for confirmation
    (step 3). Once confirmed, call POST /projects/ to save it.

    Expected Dify agent response shape:
    {
      "marking_scheme": {
        "<criterion>": { "description": "...", "max_score": 10 },
        ...
      }
    }
    """
    file_path = _save_upload(file, prefix="scheme_source_")
    try:
        scheme_output = dify_service.analyse_marking_scheme(
            file_path, user=current_user.username
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Dify agent error: {str(exc)}")
    finally:
        # Clean up the temp file — we only needed it for Dify upload
        if os.path.exists(file_path):
            os.remove(file_path)

    return {
        "message": "Marking scheme analysed successfully.",
        "marking_scheme": scheme_output.get("marking_scheme", scheme_output),
    }


# ── Initiate chat: upload tender doc → start Dify chatbot conversation ────────

@router.post("/initiate-chat")
async def initiate_chat(
    tender_doc: UploadFile = File(..., description="Tender document (PDF or DOCX)"),
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user),
):
    """
    Upload a tender document to start a new Dify chatbot conversation.

    1. Runs the Scheme Analysis workflow → extracts marking_scheme JSON.
    2. Calls /v1/chat-messages with the scheme as context → gets conversation_id.
    3. Saves a ChatConversationTable record (user_id + conversation_id + title).

    Response shape:
    {
      "conversation_id": "<dify-uuid>",
      "message_id":      "<dify-uuid>",
      "answer":          "<assistant opening message>",
      "marking_scheme":  { ... }
    }
    """
    file_path = _save_upload(tender_doc, prefix="chat_init_")
    try:
        result = dify_service.initiate_chat_with_document(
            file_path, user=current_user.username
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Dify error: {str(exc)}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

    # Persist the conversation record so the sidebar can list it
    if result.get("conversation_id"):
        record = models.ChatConversationTable(
            user_id=current_user.id,
            conversation_id=result["conversation_id"],
            title=tender_doc.filename or "Untitled",
        )
        db.add(record)
        db.commit()

    return result


# ── Step 8-10: Score tenderer submissions against a project's marking scheme ──

@router.post("/{project_id}/score")
async def score_submissions(
    project_id: int,
    files: List[UploadFile] = File(..., description="One file per tenderer (PDF or DOCX)"),
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user),
):
    # 1. Load the project and validation
    project = db.query(models.ProjectTable).filter(
        models.ProjectTable.id == project_id,
        models.ProjectTable.owner_id == current_user.id,
    ).first()

    if not project or not project.master_requirements:
        raise HTTPException(status_code=404, detail="Project or Marking Scheme not found.")

    # 2. Create the Review Job (The NEW part for tracking)
    review_job = models.ReviewJobTable(
        project_id=project_id,
        created_by=current_user.id,
        status="processing" # We set it to processing since we start immediately
    )
    db.add(review_job)
    db.commit()
    db.refresh(review_job)

    tenderer_results = []
    saved_paths = []

    # 3. Process each tenderer file (Original Loop)
    for upload in files:
        file_path = _save_upload(upload, prefix=f"review_{review_job.id}_")
        saved_paths.append(file_path)

        try:
            # We call the Dify function. 
            # Note: If this function returns the workflow_id, we save it to the Job.
            raw_results = dify_service.score_tenderer_submission(
                file_path, project.master_requirements, user=current_user.username
            )
            
            # --- NEW: Update Job with Workflow ID from the first successful call ---
            if not review_job.workflow_id and isinstance(raw_results, dict):
                # We assume your service returns a dict containing 'workflow_id' and 'results'
                review_job.workflow_id = raw_results.get("workflow_id")
                db.commit()

            # 4. Persist each criterion result (Original Logic kept)
            # Assuming raw_results['results'] contains the list of scores
            actual_items = raw_results.get("results", []) if isinstance(raw_results, dict) else raw_results
            
            db_results = []
            is_tenderer_dq = False
            for item in actual_items:
                result_row = models.ReviewResultTable(
                    review_id=review_job.id, # Link to the new job ID
                    overall_summary_json = item
                )
                db.add(result_row)
                db_results.append(result_row)
                if result_row.is_disqualified:
                    is_tenderer_dq = True

            db.commit()
            
            # Prepare result for the final return JSON
            tenderer_results.append({
                "tenderer_file": upload.filename,
                "is_disqualified": is_tenderer_dq,
                "results": [
                    {
                        "criterion": r.criterion,
                        "score": r.score,
                        "status": r.status,
                    } for r in db_results
                ],
            })

        except Exception as exc:
            tenderer_results.append({
                "tenderer_file": upload.filename,
                "error": str(exc),
            })
            continue

    # 5. Finalize Job Status
    review_job.status = "done"
    db.commit()

    # Clean up temp files
    for path in saved_paths:
        if os.path.exists(path):
            os.remove(path)

    return {
        "job_id": review_job.id,
        "workflow_id": review_job.workflow_id,
        "status": review_job.status,
        "tenderers": tenderer_results,
    }


# ── Fetch past review results ─────────────────────────────────────────────────

@router.get("/{project_id}/history")
def get_review_history(
    project_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user),
):
    """Return all past review runs for a project."""
    project = db.query(models.ProjectTable).filter(
        models.ProjectTable.id == project_id,
        models.ProjectTable.owner_id == current_user.id,
    ).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    reviews = db.query(models.TenderReviewTable).filter(
        models.TenderReviewTable.project_id == project_id
    ).all()

    return [
        {
            "review_id": r.id,
            "tenderers": list({res.tenderer_file_name for res in r.results}),
        }
        for r in reviews
    ]


# ── Async queue-based scoring ─────────────────────────────────────────────────

def _process_job_files(job_id: int, marking_scheme: dict, username: str):
    """
    Background thread: process each ReviewJobFileTable record one-by-one.
    Opens its own DB session so it is independent from the request session.
    """
    db: Session = database.SessionLocal()
    try:
        job = db.query(models.ReviewJobTable).filter(models.ReviewJobTable.id == job_id).first()
        if not job:
            return

        job.status = "processing"
        db.commit()

        all_done = True
        for job_file in job.files:
            if job_file.status != "pending":
                continue

            job_file.status = "processing"
            db.commit()

            try:
                # Decode base64-stored bytes and score via Dify
                file_bytes = base64.b64decode(job_file.file_content) if isinstance(job_file.file_content, str) else job_file.file_content
                outputs = dify_service.score_tenderer_bytes(
                    file_bytes=file_bytes,
                    file_name=job_file.file_name,
                    marking_scheme=marking_scheme,
                    user=username,
                )
                # `overall_summary_json` is a stringified JSON from the workflow
                raw = outputs.get("overall_summary_json", outputs)
                if isinstance(raw, str):
                    parsed = json.loads(raw)
                else:
                    parsed = raw
                job_file.result_json = json.dumps(parsed)
                job_file.status = "done"
            except Exception as exc:
                job_file.status = "failed"
                job_file.error = str(exc)
                all_done = False

            db.commit()

        # Mark the overall job
        failed_count = sum(1 for f in job.files if f.status == "failed")
        job.status = "done" if failed_count == 0 else ("failed" if failed_count == len(job.files) else "partial")
        db.commit()
    finally:
        db.close()


@router.post("/{project_id}/score-async")
async def score_submissions_async(
    project_id: int,
    files: List[UploadFile] = File(..., description="One file per tenderer (PDF or DOCX)"),
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user),
):
    """
    Submit multiple tenderer files for async scoring.
    Returns a job_id immediately. Poll GET /reviews/jobs/{job_id} for status.
    """
    project = db.query(models.ProjectTable).filter(
        models.ProjectTable.id == project_id,
        models.ProjectTable.owner_id == current_user.id,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    if not project.master_requirements:
        raise HTTPException(status_code=422, detail="No marking scheme on this project.")

    marking_scheme = project.master_requirements

    # Create the job
    job = models.ReviewJobTable(project_id=project_id, created_by=current_user.id, status="pending")
    db.add(job)
    db.flush()  # get job.id without committing

    # Read file bytes into DB records — base64-encode to avoid NUL byte issues in PostgreSQL
    for upload in files:
        raw_bytes = await upload.read()
        job_file = models.ReviewJobFileTable(
            job_id=job.id,
            file_name=upload.filename,
            file_content=base64.b64encode(raw_bytes).decode("ascii"),
            status="pending",
        )
        db.add(job_file)

    db.commit()
    db.refresh(job)

    # Fire background thread (non-blocking)
    t = threading.Thread(
        target=_process_job_files,
        args=(job.id, marking_scheme, current_user.username),
        daemon=True,
    )
    t.start()

    return {
        "job_id": job.id,
        "project_id": project_id,
        "file_count": len(files),
        "status": "pending",
    }


@router.get("/jobs/{job_id}")
def get_job_status(
    job_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user),
):
    # 1. Fetch the job from local DB
    job = db.query(models.ReviewJobTable).filter(
        models.ReviewJobTable.id == job_id,
        models.ReviewJobTable.created_by == current_user.id,
    ).first()
    
    if not job or not job.workflow_id:
        raise HTTPException(status_code=404, detail="Job or Workflow ID not found.")

    # 2. Get the "Live" data from Dify
    # We pass the workflow_id to our service
    dify_data = dify_service.get_workflow_run_detail(job.workflow_id)

    # 3. Update local status if Dify is finished
    if dify_data["status"] != job.status:
        job.status = dify_data["status"]
        db.commit()

    # 4. Return only the core info and the Dify "outputs"
    return {
        "job_id": job.id,
        "workflow_id": job.workflow_id,
        "status": dify_data["status"], # succeeded, failed, or running
        "created_at": dify_data["created_at"],
        "outputs": dify_data.get("outputs"), # This is the JSON output from your Dify nodes
        "error": dify_data.get("error")
    }