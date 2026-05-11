from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta
from .. import models, database, auth  # Importing our engine and database

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

@router.post("/register")
def register_user(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(database.get_db)):
    # Check if the username is taken
    user_exists = db.query(models.UserTable).filter(models.UserTable.username == form_data.username).first()
    if user_exists:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    # Hash the password using our tool in app/auth.py
    hashed_password = auth.get_password_hash(form_data.password)
    
    new_user = models.UserTable(
        username=form_data.username,
        hashed_password=hashed_password
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User created successfully", "username": new_user.username}

@router.post("/login")
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(database.get_db)):
    # 1. Find the user
    user = db.query(models.UserTable).filter(models.UserTable.username == form_data.username).first()
    
    # 2. Verify password
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Create the JWT Token
    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    # Standard OAuth2 response format
    return {"access_token": access_token, "token_type": "bearer"}