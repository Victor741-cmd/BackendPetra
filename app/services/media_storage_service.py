import mimetypes
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

import requests
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
)

from app.config import GRAPH_API_VERSION, WHATSAPP_TOKEN


class MediaStorageError(Exception):
    """Controlled error while retrieving or storing WhatsApp media."""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise MediaStorageError(f"Missing environment variable: {name}")
    return value


def get_container_name() -> str:
    return os.getenv("AZURE_STORAGE_CONTAINER", "whatsapp-media").strip()


def get_max_media_size_bytes() -> int:
    raw = os.getenv("MAX_MEDIA_SIZE_MB", "50").strip()
    try:
        size_mb = int(raw)
    except ValueError as exc:
        raise MediaStorageError("MAX_MEDIA_SIZE_MB must be an integer") from exc

    if size_mb <= 0:
        raise MediaStorageError("MAX_MEDIA_SIZE_MB must be greater than zero")

    return size_mb * 1024 * 1024


def get_blob_service_client() -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(
        _required_env("AZURE_STORAGE_CONNECTION_STRING")
    )


def ensure_private_container():
    container_client = get_blob_service_client().get_container_client(
        get_container_name()
    )

    try:
        container_client.create_container()
    except ResourceExistsError:
        pass

    return container_client


def sanitize_filename(filename: str | None) -> str | None:
    if not filename:
        return None

    clean = PurePosixPath(filename).name
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", clean).strip("._-")
    return clean[:180] or None


def extension_from_mime_type(mime_type: str | None) -> str:
    normalized = (
        mime_type.split(";")[0].strip().lower()
        if mime_type
        else ""
    )

    known = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/3gpp": ".3gp",
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
        "application/pdf": ".pdf",
    }

    return known.get(normalized) or mimetypes.guess_extension(normalized) or ".bin"


def build_blob_name(
    *,
    conversation_id: int,
    message_id: int,
    message_type: str,
    mime_type: str | None,
    filename: str | None,
) -> str:
    now = datetime.now(timezone.utc)
    clean_filename = sanitize_filename(filename)

    if clean_filename:
        final_name = f"{message_id}-{uuid.uuid4().hex[:12]}-{clean_filename}"
    else:
        final_name = (
            f"{message_id}-{uuid.uuid4().hex}"
            f"{extension_from_mime_type(mime_type)}"
        )

    return (
        f"conversations/{conversation_id}/"
        f"{now:%Y/%m/%d}/{message_type}/{final_name}"
    )


def get_meta_media_info(media_id: str) -> dict:
    if not media_id:
        raise MediaStorageError("Missing WhatsApp media ID")

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}"

    params = {}
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    if phone_number_id:
        params["phone_number_id"] = phone_number_id

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
        params=params,
        timeout=30,
    )

    if not response.ok:
        raise MediaStorageError(
            "Meta media metadata request failed: "
            f"HTTP {response.status_code} {response.text[:500]}"
        )

    data = response.json()

    if not data.get("url"):
        raise MediaStorageError("Meta did not return a media download URL")

    return data


def download_meta_media(media_url: str) -> tuple[bytes, str | None]:
    response = requests.get(
        media_url,
        headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
        timeout=(15, 120),
        stream=True,
        allow_redirects=True,
    )

    if not response.ok:
        raise MediaStorageError(
            "Meta media download failed: "
            f"HTTP {response.status_code} {response.text[:500]}"
        )

    max_size = get_max_media_size_bytes()

    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_size:
                raise MediaStorageError(
                    f"Media exceeds configured limit of {max_size} bytes"
                )
        except ValueError:
            pass

    chunks: list[bytes] = []
    downloaded = 0

    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue

        downloaded += len(chunk)

        if downloaded > max_size:
            raise MediaStorageError(
                f"Media exceeds configured limit of {max_size} bytes"
            )

        chunks.append(chunk)

    content = b"".join(chunks)

    if not content:
        raise MediaStorageError("Downloaded media file is empty")

    return content, response.headers.get("Content-Type")


def store_whatsapp_media(
    *,
    media_id: str,
    conversation_id: int,
    message_id: int,
    message_type: str,
    webhook_mime_type: str | None,
    filename: str | None,
) -> dict:
    meta_info = get_meta_media_info(media_id)
    content, downloaded_content_type = download_meta_media(meta_info["url"])

    mime_type = (
        webhook_mime_type
        or meta_info.get("mime_type")
        or downloaded_content_type
        or "application/octet-stream"
    )

    blob_name = build_blob_name(
        conversation_id=conversation_id,
        message_id=message_id,
        message_type=message_type,
        mime_type=mime_type,
        filename=filename,
    )

    safe_filename = sanitize_filename(filename)

    blob_client = ensure_private_container().get_blob_client(blob_name)

    blob_client.upload_blob(
        content,
        overwrite=False,
        content_settings=ContentSettings(
            content_type=mime_type,
            content_disposition=(
                f'inline; filename="{safe_filename}"'
                if safe_filename
                else "inline"
            ),
            cache_control="private, max-age=300",
        ),
        metadata={
            "whatsapp_media_id": media_id[:256],
            "message_id": str(message_id),
            "conversation_id": str(conversation_id),
            "message_type": message_type[:128],
        },
    )

    return {
        "blob_name": blob_name,
        "size": len(content),
        "mime_type": mime_type,
        "stored_at": datetime.utcnow(),
    }


def _storage_account_credentials() -> tuple[str, str]:
    connection_string = _required_env("AZURE_STORAGE_CONNECTION_STRING")
    values: dict[str, str] = {}

    for segment in connection_string.split(";"):
        if "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        values[key] = value

    account_name = values.get("AccountName")
    account_key = values.get("AccountKey")

    if not account_name or not account_key:
        raise MediaStorageError(
            "Storage connection string must include AccountName and AccountKey"
        )

    return account_name, account_key


def generate_private_blob_url(blob_name: str) -> str:
    if not blob_name:
        raise MediaStorageError("Missing blob name")

    account_name, account_key = _storage_account_credentials()

    try:
        minutes = int(os.getenv("MEDIA_SAS_MINUTES", "10"))
    except ValueError:
        minutes = 10

    minutes = max(1, min(minutes, 60))
    now = datetime.now(timezone.utc)

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=get_container_name(),
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        start=now - timedelta(minutes=1),
        expiry=now + timedelta(minutes=minutes),
    )

    blob_client = get_blob_service_client().get_blob_client(
        container=get_container_name(),
        blob=blob_name,
    )

    return f"{blob_client.url}?{sas_token}"
