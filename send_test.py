import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
payload = {"chat_id": TELEGRAM_CHAT_ID, "text": "🚀 Prueba de alerta desde arb-scanner"}
r = requests.post(url, data=payload)
print(r.json())
