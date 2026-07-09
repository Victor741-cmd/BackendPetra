from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.crm import Advisor
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateInitialAdminRequest(BaseModel):
    name: str = "Administrador"
    username: str = "admin"
    password: str = "123456"


def user_to_dict(user: Advisor):
    return {
        "id": user.id,
        "name": user.name,
        "username": user.email,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "is_active": user.is_active,
    }


def authenticate_user(db: Session, username: str, password: str):
    identifier = username.strip().lower()

    user = (
        db.query(Advisor)
        .filter(
            or_(
                Advisor.email == identifier,
                Advisor.name == username.strip(),
            )
        )
        .first()
    )

    if not user:
        return None

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    if not verify_password(password, user.password_hash):
        return None

    return user


def build_login_response(user: Advisor):
    token = create_access_token(
        {
            "sub": str(user.id),
            "role": user.role,
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user_to_dict(user),
    }


@router.post("/create-initial-admin")
def create_initial_admin(
    data: CreateInitialAdminRequest,
    db: Session = Depends(get_db),
):
    existing_admin = db.query(Advisor).filter(Advisor.role == "admin").first()

    if existing_admin:
        return {
            "status": "exists",
            "message": "An admin user already exists",
            "admin": user_to_dict(existing_admin),
        }

    username = data.username.strip().lower()

    existing_user = db.query(Advisor).filter(Advisor.email == username).first()

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Username already exists",
        )

    admin = Advisor(
        name=data.name.strip(),
        email=username,
        password_hash=hash_password(data.password),
        role="admin",
        status="available",
        is_active=True,
    )

    db.add(admin)
    db.commit()
    db.refresh(admin)

    return {
        "status": "ok",
        "message": "Initial admin created",
        "admin": user_to_dict(admin),
    }


@router.post("/login")
def login_for_swagger(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = authenticate_user(
        db=db,
        username=form_data.username,
        password=form_data.password,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    return build_login_response(user)


@router.post("/login-json")
def login_json(
    data: LoginRequest,
    db: Session = Depends(get_db),
):
    user = authenticate_user(
        db=db,
        username=data.username,
        password=data.password,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    return build_login_response(user)


@router.get("/me")
def get_me(current_user: Advisor = Depends(get_current_user)):
    return user_to_dict(current_user)