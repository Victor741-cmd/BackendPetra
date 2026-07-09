import requests
from app.config import GRAPH_API_VERSION, WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID


def send_text_message(to: str, message: str) -> dict:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "body": message
        }
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)

    try:
        response_body = response.json()
    except Exception:
        response_body = {"raw": response.text}

    if response.status_code >= 400:
        raise Exception(f"WhatsApp API error {response.status_code}: {response_body}")

    return response_body