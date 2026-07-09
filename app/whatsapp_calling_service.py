import requests

from app.config import (
    CALLING_API_ENABLED,
    GRAPH_API_VERSION,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_TOKEN,
)


class WhatsAppCallingDisabled(Exception):
    pass


def _ensure_calling_enabled():
    if not CALLING_API_ENABLED:
        raise WhatsAppCallingDisabled(
            "Calling API is disabled. Set CALLING_API_ENABLED=true in .env"
        )


def _graph_headers():
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }


def _graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path.lstrip('/')}"


def _safe_json(response: requests.Response):
    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


def get_calling_settings() -> dict:
    _ensure_calling_enabled()

    url = _graph_url(f"{WHATSAPP_PHONE_NUMBER_ID}/settings")

    response = requests.get(
        url,
        headers=_graph_headers(),
        timeout=30,
    )

    response_body = _safe_json(response)

    if response.status_code >= 400:
        raise Exception(f"Meta settings error {response.status_code}: {response_body}")

    return response_body


def enable_calling_settings() -> dict:
    _ensure_calling_enabled()

    url = _graph_url(f"{WHATSAPP_PHONE_NUMBER_ID}/settings")

    payload = {
        "calling": {
            "status": "ENABLED",
        }
    }

    response = requests.post(
        url,
        headers=_graph_headers(),
        json=payload,
        timeout=30,
    )

    response_body = _safe_json(response)

    if response.status_code >= 400:
        raise Exception(f"Meta settings error {response.status_code}: {response_body}")

    return response_body


def get_call_permission(user_wa_id: str) -> dict:
    _ensure_calling_enabled()

    url = _graph_url(f"{WHATSAPP_PHONE_NUMBER_ID}/call_permissions")

    response = requests.get(
        url,
        headers=_graph_headers(),
        params={
            "user_wa_id": user_wa_id,
        },
        timeout=30,
    )

    response_body = _safe_json(response)

    if response.status_code >= 400:
        raise Exception(
            f"Meta call permission error {response.status_code}: {response_body}"
        )

    return response_body


def send_call_permission_request(to: str, body_text: str) -> dict:
    _ensure_calling_enabled()

    url = _graph_url(f"{WHATSAPP_PHONE_NUMBER_ID}/messages")

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "call_permission_request",
            "body": {
                "text": body_text,
            },
            "action": {
                "name": "call_permission_request",
            },
        },
    }

    response = requests.post(
        url,
        headers=_graph_headers(),
        json=payload,
        timeout=30,
    )

    response_body = _safe_json(response)

    if response.status_code >= 400:
        raise Exception(
            f"Meta call permission request error {response.status_code}: {response_body}"
        )

    return response_body


def connect_business_call(
    to: str,
    sdp_offer: str,
    call_type: str = "audio",
    opaque_callback_data: str | None = None,
) -> dict:
    _ensure_calling_enabled()

    url = _graph_url(f"{WHATSAPP_PHONE_NUMBER_ID}/calls")

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "action": "connect",
        "session": {
            "sdp_type": "offer",
            "sdp": sdp_offer,
        },
    }

    if call_type:
        payload["call_type"] = call_type

    if opaque_callback_data:
        payload["biz_opaque_callback_data"] = opaque_callback_data

    response = requests.post(
        url,
        headers=_graph_headers(),
        json=payload,
        timeout=30,
    )

    response_body = _safe_json(response)

    if response.status_code >= 400:
        raise Exception(f"Meta call connect error {response.status_code}: {response_body}")

    return response_body


def terminate_call(call_id: str) -> dict:
    _ensure_calling_enabled()

    url = _graph_url(f"{WHATSAPP_PHONE_NUMBER_ID}/calls")

    payload = {
        "messaging_product": "whatsapp",
        "call_id": call_id,
        "action": "terminate",
    }

    response = requests.post(
        url,
        headers=_graph_headers(),
        json=payload,
        timeout=30,
    )

    response_body = _safe_json(response)

    if response.status_code >= 400:
        raise Exception(
            f"Meta call terminate error {response.status_code}: {response_body}"
        )

    return response_body