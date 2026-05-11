from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from . import database
from .models import *
from .routes import projects, tenders, auth, reviews
from .seed import seed_basic_data
from pydantic import BaseModel
import os, shutil
import requests

# Create the database tables
database.Base.metadata.create_all(bind=database.engine)

app = FastAPI()

# Allow the Vue frontend to call the API from a different origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# This "mounts" your folder so it can be accessed via URL
app.mount("/download", StaticFiles(directory="uploaded_tenders"), name="download")

DIFY_API_URL = "http://192.168.8.162/v1/completion-messages"
DIFY_API_KEY = "app-UnhDDkWMmnpIj70EcEVfkomo"

# Include our split-out routes
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(tenders.router)
app.include_router(reviews.router)

# Define where to save files
UPLOAD_DIR = "uploaded_tenders"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

# Startup logic to ensure your Lookup Table has data
@app.on_event("startup")
def startup_event():
    db = database.SessionLocal()
    if db.query(StatusTable).count() == 0:
        statuses = [
            StatusTable(id=TenderStatus.PENDING.value, name="Pending", description="Waiting for AI"),
            StatusTable(id=TenderStatus.VERIFIED.value, name="Verified", description="AI Approved"),
            StatusTable(id=TenderStatus.REJECTED.value, name="Rejected", description="AI Rejected")
        ]
        db.add_all(statuses)
        db.commit()

    # Seed the dummy POC user (skipped automatically if a user already exists)
    seed_basic_data(db)
    db.close()

@app.post("/submit")
def submit_tender(company: str, title: str, amount: float, db: Session = Depends(database.get_db)):
    new_tender = TenderTable(
        company_name=company,
        tender_title=title,
        bid_amount=amount
    )
    db.add(new_tender)
    db.commit()
    return {"message": "Success", "id": new_tender.id}

@app.patch("/tenders/{tender_id}/verify")
def verify_tender(tender_id: int, db: Session = Depends(database.get_db)):
    """Endpoint for your AI Agent colleague to call"""
    db_tender = db.query(TenderTable).filter(TenderTable.id == tender_id).first()
    if not db_tender:
        raise HTTPException(status_code=404, detail="Tender not found")
    
    # Transition the status to Verified
    db_tender.status_id = TenderStatus.VERIFIED.value
    db.commit()
    
    return {
        "tender_id": tender_id, 
        "new_status": TenderStatus.VERIFIED.name,
        "detail": db_tender.status_info.description # Pulls from the Lookup Table!
    }

@app.patch("/tenders/{tender_id}/update-status")
def update_tender_status(
    tender_id: int = Path(..., description="The ID of the tender to update"),
    new_status: TenderStatus = Query(..., description="Choose 1 for Pending, 2 for Verified, 3 for Rejected"),
    db: Session = Depends(database.get_db)
):
    # 1. Look for the tender in the database
    db_tender = db.query(TenderTable).filter(TenderTable.id == tender_id).first()
    
    # 2. If it doesn't exist, tell the caller (the AI agent)
    if not db_tender:
        raise HTTPException(status_code=404, detail="Tender not found")
    
    # 3. Update the status_id using our Hybrid Enum
    db_tender.status_id = new_status.value
    db.commit()
    db.refresh(db_tender)
    
    # 4. Return a clear response
    return {
        "message": "Status updated successfully",
        "tender_id": db_tender.id,
        "new_status": db_tender.status_info.name,  # Uses the Relationship to get the name
        "description": db_tender.status_info.description
    }

@app.get("/tenders")
def list_tenders(db: Session = Depends(database.get_db)):
    tenders = db.query(TenderTable).all()
    
    # We create a nice list that includes the status name instead of just the ID number
    return [
        {
            "id": t.id,
            "company": t.company_name,
            "title": t.tender_title,
            "amount": t.bid_amount,
            "status": t.status_info.name  # This is why we created the Relationship!
        }
        for t in tenders
    ]

@app.get("/tenders/{tender_id}")
def get_single_tender(tender_id: int, db: Session = Depends(database.get_db)):
    tender = db.query(models.TenderTable).filter(models.TenderTable.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found")
    
    return {
        "id": tender.id,
        "company": tender.company_name,
        "title": tender.tender_title,
        "amount": tender.bid_amount,
        "status": tender.status_info.name,
        "attachments": [
            {"id": a.id, "name": a.file_name, "path": a.file_path} 
            for a in tender.attachments
        ]
    }

@app.put("/tenders/{tender_id}")
def update_tender(tender_id: int, company_name: str, tender_title: str, bid_amount: float, db: Session = Depends(database.get_db)):
    db_tender = db.query(models.TenderTable).filter(models.TenderTable.id == tender_id).first()
    if not db_tender:
        raise HTTPException(status_code=404, detail="Tender not found")
    
    db_tender.company_name = company_name
    db_tender.tender_title = tender_title
    db_tender.bid_amount = bid_amount
    
    db.commit()
    return {"message": "Tender updated"}

@app.post("/tenders/{tender_id}/upload-document")
def upload_tender_document(
    tender_id: int, 
    file: UploadFile = File(...), 
    db: Session = Depends(database.get_db)
):
    # 1. Check if the tender exists in the DB
    db_tender = db.query(TenderTable).filter(TenderTable.id == tender_id).first()
    if not db_tender:
        raise HTTPException(status_code=404, detail="Tender record not found")

    # 2. Create a safe file path
    # We prefix with tender_id to prevent name conflicts
    file_name = f"ID_{tender_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, file_name)

    # 3. Save the file to your hard drive
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save file: {str(e)}")

    # 4. Update the database with the file path
    db_tender.file_path = file_path
    db.commit()

    return {
        "message": "File uploaded and linked successfully",
        "file_path": file_path,
        "tender_id": tender_id
    }

@app.post("/tenders/{tender_id}/attachments")
def add_attachment(tender_id: int, file: UploadFile = File(...), db: Session = Depends(database.get_db)):
    # 1. Save file to disk (standard logic)
    file_path = f"uploaded_tenders/ID_{tender_id}_{file.filename}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # 2. Add record to AttachmentTable
    new_attachment = models.AttachmentTable(
        file_name=file.filename,
        file_path=file_path,
        tender_id=tender_id
    )
    db.add(new_attachment)
    db.commit()
    return {"message": "Attachment added", "attachment_id": new_attachment.id}

@app.delete("/attachments/{attachment_id}")
def remove_attachment(attachment_id: int, db: Session = Depends(database.get_db)):
    attachment = db.query(models.AttachmentTable).filter(models.AttachmentTable.id == attachment_id).first()
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    
    # Optional: Delete the actual file from Windows
    if os.path.exists(attachment.file_path):
        os.remove(attachment.file_path)
        
    db.delete(attachment)
    db.commit()
    return {"message": "Attachment removed successfully"}
