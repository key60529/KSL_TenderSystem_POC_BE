"""
Conversations router — manages the list of Dify chat conversations per user.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from .. import models, database, auth

router = APIRouter(prefix="/conversations", tags=["Conversations"])


@router.get("/")
def list_conversations(
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user),
):
    """
    Return all chat conversations belonging to the current user,
    ordered newest first.

    Response shape:
    [
      {
        "id": 1,
        "conversation_id": "<dify-uuid>",
        "title": "tender_document.pdf",
        "created_at": "2026-05-12T10:00:00Z"
      },
      ...
    ]
    """
    rows = (
        db.query(models.ChatConversationTable)
        .filter(models.ChatConversationTable.user_id == current_user.id)
        .order_by(models.ChatConversationTable.created_at.desc())
        .all()
    )

    return [
        {
            "id": row.id,
            "conversation_id": row.conversation_id,
            "title": row.title,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.UserTable = Depends(auth.get_current_user),
):
    """Delete a conversation record by its Dify conversation_id."""
    row = (
        db.query(models.ChatConversationTable)
        .filter(
            models.ChatConversationTable.conversation_id == conversation_id,
            models.ChatConversationTable.user_id == current_user.id,
        )
        .first()
    )

    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    db.delete(row)
    db.commit()
    return {"message": "Conversation deleted."}
