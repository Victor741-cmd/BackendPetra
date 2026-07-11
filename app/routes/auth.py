from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.login_crypto import (
    LoginCryptoError,
    decrypt_login_payload,
    get_login_public_key_pem,
)
from app.models.crm import Advisor
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class SecureLoginRequest(BaseModel):
    encrypted_key: str = Field(min_length=1, max_length=4096)
    iv: str = Field(min_length=1, max_length=256)
    ciphertext: str = Field(min_length=1, max_length=16384)


class CreateInitialAdminRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=12, max_length=256)


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


def authenticate_user(
    db: Session,
    username: str,
    password: str,
):
    clean_username = username.strip()
    identifier = clean_username.lower()

    user = (
        db.query(Advisor)
        .filter(
            or_(
                func.lower(Advisor.email) == identifier,
                func.lower(Advisor.name) == identifier,
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


def perform_login(
    db: Session,
    username: str,
    password: str,
):
    user = authenticate_user(
        db=db,
        username=username,
        password=password,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return build_login_response(user)


@router.get("/login-public-key")
def login_public_key():
    """
    Entrega exclusivamente la llave pública RSA.

    La llave privada permanece en la variable de entorno
    LOGIN_PRIVATE_KEY_B64 del backend.
    """
    try:
        public_key = get_login_public_key_pem()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Secure login is not configured",
        )

    return {
        "algorithm": "RSA-OAEP",
        "hash": "SHA-256",
        "public_key": public_key,
    }


@router.post("/login-secure")
def login_secure(
    data: SecureLoginRequest,
    db: Session = Depends(get_db),
):
    """
    Recibe el login cifrado mediante:

    - RSA-OAEP SHA-256 para la clave AES.
    - AES-GCM para el contenido del login.
    """
    try:
        credentials = decrypt_login_payload(
            encrypted_key_b64=data.encrypted_key,
            iv_b64=data.iv,
            ciphertext_b64=data.ciphertext,
        )
    except LoginCryptoError:
        # No se devuelve información específica del descifrado
        # para evitar filtrar detalles internos.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid encrypted login payload",
        )
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Secure login is not configured",
        )

    return perform_login(
        db=db,
        username=credentials["username"],
        password=credentials["password"],
    )


@router.post("/create-initial-admin")
def create_initial_admin(
    data: CreateInitialAdminRequest,
    db: Session = Depends(get_db),
):
    existing_admin = (
        db.query(Advisor)
        .filter(Advisor.role == "admin")
        .first()
    )

    if existing_admin:
        return {
            "status": "exists",
            "message": "An admin user already exists",
            "admin": user_to_dict(existing_admin),
        }

    username = data.username.strip().lower()
    name = data.name.strip()

    existing_user = (
        db.query(Advisor)
        .filter(func.lower(Advisor.email) == username)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    admin = Advisor(
        name=name,
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
    """
    Login tradicional utilizado por Swagger/OAuth2.

    No será utilizado por el frontend de producción.
    """
    return perform_login(
        db=db,
        username=form_data.username,
        password=form_data.password,
    )


@router.post("/login-json", deprecated=True)
def login_json(
    data: LoginRequest,
    db: Session = Depends(get_db),
):
    """
    Endpoint anterior conservado temporalmente por compatibilidad.

    El frontend nuevo utilizará /auth/login-secure.
    """
    return perform_login(
        db=db,
        username=data.username,
        password=data.password,
    )


@router.get("/me")
def get_me(
    current_user: Advisor = Depends(get_current_user),
):
    return user_to_dict(current_user)
