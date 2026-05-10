from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session
from .. import models, database, auth
from ..services import dify_service
import os

# 1. Create the router object
router = APIRouter(
    prefix="/tenders",
    tags=["Tenders"]
)

# 2. Use @router instead of @app
@router.post("/{tender_id}/generate-draft")
def generate_draft(tender_id: int, db: Session = Depends(database.get_db)):
    tender = db.query(models.TenderTable).filter(models.TenderTable.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found")

    # Call the service
    draft_text = dify_service.get_ai_draft(tender.company_name, tender.tender_title, tender.bid_amount)

    # Save logic...
    file_name = f"Draft_{tender_id}.txt"
    file_path = f"uploaded_tenders/{file_name}"
    with open(file_path, "w") as f:
        f.write(draft_text)

    # Add attachment record...
    new_attachment = models.AttachmentTable(file_name=file_name, file_path=file_path, tender_id=tender_id)
    db.add(new_attachment)
    db.commit()
    
    return {"status": "Draft created", "file": file_name}

@router.post("/{tender_id}/upload-requirement")
async def upload_requirement(
    tender_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: models.UserTable = Depends(auth.get_current_user)
):
    # 1. Save the file locally first (so we don't lose it)
    file_location = f"temp_storage/{file.filename}"
    with open(file_location, "wb+") as file_object:
        file_object.write(file.file.read())

    # 2. Add the Dify upload to the background
    # This means the user gets a response NOW, and the heavy work happens later.
    background_tasks.add_task(
        dify_service.upload_and_process_requirement, 
        tender_id, 
        file_location
    )

    return {"message": "File received. Processing in background...", "filename": file.filename}