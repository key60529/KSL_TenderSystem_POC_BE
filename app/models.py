import enum
from sqlalchemy import Column, Integer, String, Float, ForeignKey, JSON, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base  # Note the "." - it means "from this same folder"

from sqlalchemy import Column, Integer, String, ForeignKey, JSON
from sqlalchemy.orm import relationship
from .database import Base

class UserTable(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    
    # One User -> Many Projects
    projects = relationship("ProjectTable", back_populates="owner")

class ProjectTable(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    description = Column(String, nullable=True)
    # Stores the AI-generated marking scheme as JSON
    master_requirements = Column(JSONB, nullable=True) 
    dify_conversation_id = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"))

    owner = relationship("UserTable", back_populates="projects")
    # One Project -> Many Tenders
    tenders = relationship("TenderTable", back_populates="parent_project")
    # One Project -> Many review runs
    reviews = relationship("TenderReviewTable", back_populates="project", cascade="all, delete-orphan")

class TenderStatus(int, enum.Enum):
    PENDING = 1
    VERIFIED = 2
    REJECTED = 3

class StatusTable(Base):
    __tablename__ = "tender_statuses"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)

class TenderTable(Base):
    __tablename__ = "tenders"
    
    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String)
    tender_title = Column(String)
    bid_amount = Column(Float)
    status_id = Column(Integer, ForeignKey("tender_statuses.id"), default=1)
    
    # Relationships
    status_info = relationship("StatusTable")

    # New Link: Tender belongs to a Project
    project_id = Column(Integer, ForeignKey("projects.id"))
    parent_project = relationship("ProjectTable", back_populates="tenders")

    # New: This allows tender.attachments to return a list of files
    attachments = relationship("AttachmentTable", back_populates="parent_tender", cascade="all, delete-orphan")

class AttachmentTable(Base):
    __tablename__ = "tender_attachments"
    
    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String)
    file_path = Column(String)
    tender_id = Column(Integer, ForeignKey("tenders.id"))
    
    # Link back to the tender
    parent_tender = relationship("TenderTable", back_populates="attachments")

# ── NEW: Review / Scoring models ──────────────────────────────────────────────

class TenderReviewTable(Base):
    """One review run = one project + one or more tenderer files submitted together."""
    __tablename__ = "tender_reviews"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    created_by = Column(Integer, ForeignKey("users.id"))

    project = relationship("ProjectTable", back_populates="reviews")
    results = relationship("ReviewResultTable", back_populates="review", cascade="all, delete-orphan")

class ReviewResultTable(Base):
    """Per-tenderer, per-criterion result returned by the Dify scoring agent."""
    __tablename__ = "review_results"
    id = Column(Integer, primary_key=True, index=True)
    review_id = Column(Integer, ForeignKey("tender_reviews.id"))
    tenderer_file_name = Column(String)           # original uploaded filename
    criterion = Column(String)                     # marking scheme criterion label
    score = Column(Float, nullable=True)           # numeric score (null = not scored)
    max_score = Column(Float, nullable=True)       # maximum possible score
    status = Column(String)                        # 'pass' | 'fail' | 'dq'
    is_disqualified = Column(Boolean, default=False)
    dq_reason = Column(String, nullable=True)
    evidence = Column(String, nullable=True)       # quote / location in document
    comment = Column(String, nullable=True)        # LLM reasoning

    review = relationship("TenderReviewTable", back_populates="results")


# ── Chat Conversation records ─────────────────────────────────────────────────

class ChatConversationTable(Base):
    """Stores Dify conversation IDs per user so the sidebar can list them."""
    __tablename__ = "chat_conversations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    conversation_id = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=True)          # filename used as display title
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    owner = relationship("UserTable", backref="conversations")


# ── Async scoring job queue ───────────────────────────────────────────────────

class ReviewJobTable(Base):
    """One async scoring run (one project, multiple files)."""
    __tablename__ = "review_jobs"
    
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # NEW FIELD: Stores the Dify workflow execution ID
    workflow_id = Column(String, nullable=True) 
    
    # overall job status: pending | processing | done | failed
    status = Column(String, default="pending")

    # Relationships
    files = relationship("ReviewJobFileTable", back_populates="job", cascade="all, delete-orphan")


class ReviewJobFileTable(Base):
    """Per-file status and result within a ReviewJob."""
    __tablename__ = "review_job_files"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("review_jobs.id"), nullable=False)
    file_name = Column(String, nullable=False)
    file_content = Column(Text, nullable=True)     # stored temporarily for background processing
    # status: pending | processing | done | failed
    status = Column(String, default="pending")
    result_json = Column(Text, nullable=True)      # parsed overall_summary_json stored as text
    error = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    job = relationship("ReviewJobTable", back_populates="files")