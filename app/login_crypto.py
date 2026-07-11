import base64
import binascii
import json
import os
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class LoginCryptoError(Exception):
    """Error controlado durante el descifrado del login."""


def _decode_base64(value: str, field_name: str) -> bytes:
    if not value or not isinstance(value, str):
        raise LoginCryptoError(f"Missing encrypted field: {field_name}")

    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LoginCryptoError(
            f"Invalid Base64 value for {field_name}"
        ) from exc


@lru_cache(maxsize=1)
def get_login_private_key() -> rsa.RSAPrivateKey:
    """
    Carga la llave privada RSA desde LOGIN_PRIVATE_KEY_B64.

    La variable contiene el archivo PEM completo codificado en Base64.
    La llave queda cacheada en memoria después de la primera carga.
    """
    private_key_b64 = os.getenv("LOGIN_PRIVATE_KEY_B64", "").strip()

    if not private_key_b64:
        raise RuntimeError(
            "LOGIN_PRIVATE_KEY_B64 environment variable is not configured"
        )

    try:
        private_key_pem = base64.b64decode(
            private_key_b64,
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(
            "LOGIN_PRIVATE_KEY_B64 is not valid Base64"
        ) from exc

    try:
        private_key = serialization.load_pem_private_key(
            private_key_pem,
            password=None,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "LOGIN_PRIVATE_KEY_B64 does not contain a valid PEM private key"
        ) from exc

    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise RuntimeError(
            "LOGIN_PRIVATE_KEY_B64 must contain an RSA private key"
        )

    if private_key.key_size < 2048:
        raise RuntimeError(
            "The RSA private key must be at least 2048 bits"
        )

    return private_key


def get_login_public_key_pem() -> str:
    """
    Obtiene la llave pública derivada de la llave privada configurada.

    Esta llave sí puede entregarse al frontend.
    """
    private_key = get_login_private_key()

    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    return public_key_pem.decode("utf-8")


def decrypt_login_payload(
    encrypted_key_b64: str,
    iv_b64: str,
    ciphertext_b64: str,
) -> dict[str, Any]:
    """
    Descifra el payload híbrido enviado por el frontend.

    Proceso:
    1. RSA-OAEP descifra la clave AES.
    2. AES-GCM descifra y autentica el JSON.
    3. Se valida username y password.
    """
    private_key = get_login_private_key()

    encrypted_key = _decode_base64(
        encrypted_key_b64,
        "encrypted_key",
    )
    iv = _decode_base64(
        iv_b64,
        "iv",
    )
    ciphertext = _decode_base64(
        ciphertext_b64,
        "ciphertext",
    )

    if len(iv) != 12:
        raise LoginCryptoError("Invalid AES-GCM IV length")

    try:
        aes_key = private_key.decrypt(
            encrypted_key,
            padding.OAEP(
                mgf=padding.MGF1(
                    algorithm=hashes.SHA256(),
                ),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except ValueError as exc:
        raise LoginCryptoError(
            "Unable to decrypt the login encryption key"
        ) from exc

    if len(aes_key) not in (16, 24, 32):
        raise LoginCryptoError("Invalid AES key length")

    try:
        plaintext = AESGCM(aes_key).decrypt(
            iv,
            ciphertext,
            None,
        )
    except Exception as exc:
        raise LoginCryptoError(
            "Unable to decrypt or authenticate login payload"
        ) from exc

    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoginCryptoError(
            "Decrypted login payload is not valid JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise LoginCryptoError(
            "Decrypted login payload must be an object"
        )

    username = payload.get("username")
    password = payload.get("password")

    if not isinstance(username, str) or not username.strip():
        raise LoginCryptoError("Invalid username")

    if not isinstance(password, str) or not password:
        raise LoginCryptoError("Invalid password")

    if len(username) > 150:
        raise LoginCryptoError("Username is too long")

    if len(password) > 256:
        raise LoginCryptoError("Password is too long")

    return {
        "username": username.strip(),
        "password": password,
    }
