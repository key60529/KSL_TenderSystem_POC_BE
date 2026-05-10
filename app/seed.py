import os
from sqlalchemy.orm import Session
from . import models
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# POC dummy credentials — override via environment variables before going to production.
SEED_USERNAME = os.getenv("SEED_USERNAME", "admin_user")
SEED_PASSWORD = os.getenv("SEED_PASSWORD", "password123")

def seed_basic_data(db: Session):
    # Skip seeding if any user already exists.
    if db.query(models.UserTable).first():
        print("Seed skipped: user(s) already exist.")
        return

    # Create the dummy POC user.
    hashed_pw = pwd_context.hash(SEED_PASSWORD)
    demo_user = models.UserTable(username=SEED_USERNAME, hashed_password=hashed_pw)
    db.add(demo_user)
    db.commit()
    db.refresh(demo_user)

    # Create a sample project linked to the dummy user.
    sample_project = models.ProjectTable(
        title="Office Renovation 2026",
        description="Main project for HQ renovation",
        owner_id=demo_user.id,
        master_requirements={
            "min_budget": 10000,
            "required_certification": "ISO9001",
            "deadline": "2026-12-31"
        }
    )
    db.add(sample_project)
    db.commit()
    print(f"Seed complete: user '{SEED_USERNAME}' created with sample project.")