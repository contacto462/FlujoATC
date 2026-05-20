import requests
from ATC.app.core.config import settings


def send_whatsapp_message(to_phone: str, body: str):
    url = f"https://graph.facebook.com/v18.0/{settings.WA_PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {settings.WA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": body},
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code >= 400:
        raise Exception(f"WhatsApp error: {response.text}")

    return response.json()
