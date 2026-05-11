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
import os, shutil, json, threading

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
    """
    Upload one file per tenderer. Each file is sent to the Dify Scoring agent
    together with the project's saved marking scheme.

    Returns per-tenderer, per-criterion scores and DQ flags.

    Expected Dify agent response per file:
    {
      "results": [
        {
          "criterion": "...",
          "score": 8, "max_score": 10,
          "status": "pass",          // "pass" | "fail" | "dq"
          "is_disqualified": false,
          "dq_reason": null,
          "evidence": "page 3, para 2",
          "comment": "Meets requirement X."
        },
        ...
      ]
    }
    """
    # 1. Load the project and its marking scheme
    project = db.query(models.ProjectTable).filter(
        models.ProjectTable.id == project_id,
        models.ProjectTable.owner_id == current_user.id,
    ).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    if not project.master_requirements:
        raise HTTPException(
            status_code=422,
            detail="This project has no marking scheme saved. Please save one first.",
        )

    marking_scheme = project.master_requirements

    # 2. Create a review run record
    review = models.TenderReviewTable(
        project_id=project_id,
        created_by=current_user.id,
    )
    db.add(review)
    db.commit()
    db.refresh(review)

    # 3. Process each tenderer file
    tenderer_results = []
    saved_paths = []

    for upload in files:
        file_path = _save_upload(upload, prefix=f"review_{review.id}_")
        saved_paths.append(file_path)

        try:
            raw_results = dify_service.score_tenderer_submission(
                file_path, marking_scheme, user=current_user.username
            )
        except Exception as exc:
            # Record failure for this tenderer but continue with others
            tenderer_results.append({
                "tenderer_file": upload.filename,
                "error": f"Dify agent error: {str(exc)}",
                "results": [],
            })
            continue

        # 4. Persist each criterion result
        db_results = []
        is_tenderer_dq = False
        for item in raw_results:
            result_row = models.ReviewResultTable(
                review_id=review.id,
                tenderer_file_name=upload.filename,
                criterion=item.get("criterion", ""),
                score=item.get("score"),
                max_score=item.get("max_score"),
                status=item.get("status", "fail"),
                is_disqualified=item.get("is_disqualified", False),
                dq_reason=item.get("dq_reason"),
                evidence=item.get("evidence"),
                comment=item.get("comment"),
            )
            db.add(result_row)
            db_results.append(result_row)
            if result_row.is_disqualified:
                is_tenderer_dq = True

        db.commit()

        tenderer_results.append({
            "tenderer_file": upload.filename,
            "is_disqualified": is_tenderer_dq,
            "results": [
                {
                    "criterion": r.criterion,
                    "score": r.score,
                    "max_score": r.max_score,
                    "status": r.status,
                    "is_disqualified": r.is_disqualified,
                    "dq_reason": r.dq_reason,
                    "evidence": r.evidence,
                    "comment": r.comment,
                }
                for r in db_results
            ],
        })

    # 5. Clean up temp files
    for path in saved_paths:
        if os.path.exists(path):
            os.remove(path)

    return {
        "review_id": review.id,
        "project_id": project_id,
        "tenderer_count": len(files),
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
                # Decode stored bytes and score via Dify
                file_bytes = job_file.file_content.encode("latin-1") if isinstance(job_file.file_content, str) else job_file.file_content
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

    # Read file bytes into DB records (store as latin-1 str to survive JSON serialisation)
    for upload in files:
        raw_bytes = await upload.read()
        job_file = models.ReviewJobFileTable(
            job_id=job.id,
            file_name=upload.filename,
            file_content=raw_bytes.decode("latin-1"),
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
    """
    Poll the status of an async scoring job.
    Returns overall job status plus per-file statuses and (when done) result_json.
    """
    job = db.query(models.ReviewJobTable).filter(
        models.ReviewJobTable.id == job_id,
        models.ReviewJobTable.created_by == current_user.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    # Reload to get latest state (important: the background thread commits independently)
    db.refresh(job)
    for f in job.files:
        db.refresh(f)

    files_out = []
    for f in job.files:
        parsed_result = None
        if f.result_json:
            try:
                parsed_result = json.loads(f.result_json)
            except Exception:
                parsed_result = None
        files_out.append({
            "id": f.id,
            "file_name": f.file_name,
            "status": f.status,
            "result": parsed_result,
            "error": f.error,
        })

    return {
        "job_id": job.id,
        "project_id": job.project_id,
        "status": job.status,
        "files": files_out,
    }
