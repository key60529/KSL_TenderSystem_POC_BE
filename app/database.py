import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# This line says: "Use the Docker URL if it exists, otherwise use your local Windows one"
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://postgres:admin123@localhost:5432/tender_system"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL)

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# This function provides a database session for each request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()