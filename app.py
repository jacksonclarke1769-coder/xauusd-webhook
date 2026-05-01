"""
XAUUSD Webhook Bridge - Railway.app deployment
Receives TradingView alerts and sends WhatsApp via Callmebot
"""

from flask import Flask, request, jsonify
import requests
import os
import re
import logging
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

WHATSAPP_NUMBER  = os.environ.get("WHATSAPP_NUMBER", "")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY", "")


def send_whatsapp(message):
    if not WHATSAPP_NUMBER or not CALLMEBOT_APIKEY:
        log.warning("WhatsApp not configured")
        return False
    try:
        url = "https://api.callmebot.com/whatsapp.php"
        params = {"phone": WHATSAPP_NUMBER, "text": message, "apikey": CALLMEBOT_APIKEY}
        r = requests.get(url, params=params, timeout=10)
        success = r.status_code == 200
        log.info("WhatsApp sent" if success else "WhatsApp failed")
        return success
    except Exception as e:
        log.error(f"WhatsApp error: {e}")
        return False


def parse_signal(body):
    signal = {"raw": body, "setup": "?", "direction": "?", "entry": None, "sl": None, "tp": None, "lots": None, "type": "ENTRY"}
    if "TP HIT" in body:
        signal["type"] = "TP_HIT"
    elif "SL HIT" in body:
        signal["type"] = "SL_HIT"
    elif "CONFIRMED" in body:
        signal["type"] = "CONFLUENCE"
    elif "REVERSAL" in body:
        signal["type"] = "REVERSAL"
    for s in ["B LONG", "B SHORT", "C LONG", "C SHORT", "D LONG", "D SHORT"]:
        if s in body:
            signal["setup"] = s.split()[0]
            signal["direction"] = s.split()[1]
            break
    for key, pattern in [("entry","Entry"), ("sl","SL"), ("tp","TP"), ("lots","Lots")]:
        m = re.search(rf"{pattern}:([\d.]+)", body)
        if m:
            signal[key] = float(m.group(1))
    return signal


def format_message(signal):
    now = datetime.utcnow().strftime("%d/%m %H:%M UTC")
    raw = signal["raw"]
    if signal["type"] == "CONFLUENCE":
        return "CONFIRMED BIAS XAUUSD " + now + " CLOSE D re-enter higher setup Entry:" + str(signal["entry"]) + " SL:" + str(signal["sl"]) + " TP:" + str(signal["tp"])
    if signal["type"] == "REVERSAL":
        d = "LONG" if "LONG" in raw else "SHORT"
        return "REVERSAL WARNING XAUUSD " + now + " " + d + " fired AGAINST open D CLOSE D NOW Level:" + str(signal["entry"])
    if signal["type"] == "TP_HIT":
        setup = signal.get("setup","?")
        profit = {"B":1875,"C":1000,"D":250}.get(setup, 250)
        return "TP HIT XAUUSD " + now + " Setup " + setup + " Entry:" + str(signal["entry"]) + " TP:" + str(signal["tp"]) + " WINNER +$" + str(profit)
    if signal["type"] == "SL_HIT":
        setup = signal.get("setup","?")
        loss = {"B":750,"C":500,"D":100}.get(setup, 100)
        return "SL HIT XAUUSD " + now + " Setup " + setup + " Entry:" + str(signal["entry"]) + " SL:" + str(signal["sl"]) + " LOSER -$" + str(loss)
    direction = signal["direction"]
    risk = {"B":"1.5%","C":"1.0%","D":"0.2%"}.get(signal["setup"],"")
    return "XAUUSD " + signal["setup"] + " " + direction + " " + now + " Entry:" + str(signal["entry"]) + " SL:" + str(signal["sl"]) + " TP:" + str(signal["tp"]) + " Lots:" + str(signal["lots"]) + " Risk:" + risk


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "XAUUSD Webhook Bridge"}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        body = request.get_data(as_text=True).strip()
        log.info(f"Received: {body}")
        signal = parse_signal(body)
        message = format_message(signal)
        sent = send_whatsapp(message)
        return jsonify({"status": "ok" if sent else "failed", "type": signal["type"], "setup": signal["setup"]}), 200
    except Exception as e:
        log.error(f"Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
