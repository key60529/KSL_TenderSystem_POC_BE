from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional, Any, Dict
from .. import models, database, auth
from pydantic import BaseModel

router = APIRouter(
    prefix="/projects",
    tags=["Projects"]
)

# Pydantic schema for creating a project
class ProjectBase(BaseModel):
    title: str
    description: Optional[str] = None
    master_requirements: Optional[Dict[str, Any]] = None  # Accepts a JSON object
    dify_conversation_id: Optional[str] = None

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(ProjectBase):
    # Everything is optional in an update
    title: Optional[str] = None

@router.post("/", response_model=dict)
def create_project(
    project_data: ProjectCreate, 
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user)
):
    new_project = models.ProjectTable(
        title=project_data.title,
        description=project_data.description,
        owner_id=current_user.id,
        # Now taking inputs from the request:
        master_requirements=project_data.master_requirements or {}, 
        dify_conversation_id=project_data.dify_conversation_id
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    return {"id": new_project.id, "status": "Project created"}


@router.put("/{project_id}")
def update_project(
    project_id: int,
    updated_data: ProjectUpdate, 
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user)
):
    project = db.query(models.ProjectTable).filter(
        models.ProjectTable.id == project_id,
        models.ProjectTable.owner_id == current_user.id
    ).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found or unauthorized")

    # Update only the fields that were provided
    update_dict = updated_data.dict(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(project, key, value)

    db.commit()
    return {"message": "Project updated successfully"}

@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user)
):
    project = db.query(models.ProjectTable).filter(models.ProjectTable.id == project_id).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Prevent non-owners from deleting
    if project.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Only the owner can delete this project"
        )

    db.delete(project)
    db.commit()
    return {"message": "Project deleted"}

@router.get("/", response_model=List[dict])
def list_my_projects(
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user)
):
    # Only return projects belonging to this user
    projects = db.query(models.ProjectTable).filter(
        models.ProjectTable.owner_id == current_user.id
    ).all()
    
    return [
        {"id": p.id, "title": p.title, "description": p.description} 
        for p in projects
    ]

@router.get("/{project_id}")
def get_project_details(
    project_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user)
):
    project = db.query(models.ProjectTable).filter(
        models.ProjectTable.id == project_id,
        models.ProjectTable.owner_id == current_user.id
    ).first()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
        
    return project