"""
XAUUSD Webhook Bridge - Railway.app deployment
Receives TradingView alerts, sends WhatsApp via Callmebot
Weekly P&L summary every Sunday 5pm AEST (07:00 UTC)
"""

from flask import Flask, request, jsonify
import requests
import os
import re
import logging
import threading
import time
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

WHATSAPP_NUMBER  = os.environ.get("WHATSAPP_NUMBER", "")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY", "")

PROFIT_MAP = {"B": 1875.0, "C": 1000.0, "D": 250.0}
LOSS_MAP   = {"B": 750.0, "C": 500.0, "D": 100.0}

weekly_stats = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": [], "week_start": ""}
stats_lock = threading.Lock()


def reset_weekly_stats():
    with stats_lock:
        weekly_stats["wins"] = 0
        weekly_stats["losses"] = 0
        weekly_stats["pnl"] = 0.0
        weekly_stats["trades"] = []
        weekly_stats["week_start"] = datetime.utcnow().strftime("%d/%m")


def record_trade(setup, direction, result, amount):
    with stats_lock:
        if result == "WIN":
            weekly_stats["wins"] += 1
            weekly_stats["pnl"] += amount
        else:
            weekly_stats["losses"] += 1
            weekly_stats["pnl"] -= amount
        weekly_stats["trades"].append({"time": datetime.utcnow().strftime("%d/%m %H:%M"), "setup": setup, "direction": direction, "result": result, "amount": amount})


def build_weekly_summary():
    with stats_lock:
        wins = weekly_stats["wins"]
        losses = weekly_stats["losses"]
        pnl = weekly_stats["pnl"]
        total = wins + losses
        wr = (wins / total * 100) if total > 0 else 0
        start = weekly_stats["week_start"] or datetime.utcnow().strftime("%d/%m")
        trades = list(weekly_stats["trades"][-5:])
    now = datetime.utcnow().strftime("%d/%m")
    pnl_str = "+${:.0f}".format(pnl) if pnl >= 0 else "-${:.0f}".format(abs(pnl))
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    trades_log = ""
    for t in trades:
        icon = "✅" if t["result"] == "WIN" else "❌"
        trades_log += "\n  {} {} {} {}".format(icon, t["setup"], t["direction"], t["time"])
    return ("📊 WEEKLY SUMMARY — XAUUSD\n"
            + "Week: {} to {}\n".format(start, now)
            + "─────────────────\n"
            + "Trades:   {}\n".format(total)
            + "Wins:     {}\n".format(wins)
            + "Losses:   {}\n".format(losses)
            + "Win Rate: {:.1f}%\n".format(wr)
            + "P&L:      {} {}\n".format(pnl_str, pnl_emoji)
            + "─────────────────\n"
            + "Last 5 trades:{}\n".format(trades_log)
            + "─────────────────\n"
            + "New week starts now. Good luck! 💪")


def weekly_scheduler():
    last_sent_week = None
    while True:
        try:
            now = datetime.utcnow()
            if now.weekday() == 6 and now.hour == 7 and now.minute < 5:
                week_id = now.strftime("%Y-W%W")
                if week_id != last_sent_week:
                    log.info("Sending weekly summary...")
                    send_whatsapp(build_weekly_summary())
                    last_sent_week = week_id
                    reset_weekly_stats()
            time.sleep(60)
        except Exception as e:
            log.error("Scheduler error: {}".format(e))
            time.sleep(60)


threading.Thread(target=weekly_scheduler, daemon=True).start()
reset_weekly_stats()


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
        log.error("WhatsApp error: {}".format(e))
        return False


def parse_signal(body):
    signal = {"raw": body, "setup": "?", "direction": "?", "entry": None, "sl": None, "tp": None, "lots": None, "type": "ENTRY"}
    if "TP HIT" in body: signal["type"] = "TP_HIT"
    elif "SL HIT" in body: signal["type"] = "SL_HIT"
    elif "CONFIRMED" in body: signal["type"] = "CONFLUENCE"
    elif "REVERSAL" in body: signal["type"] = "REVERSAL"
    for s in ["B LONG", "B SHORT", "C LONG", "C SHORT", "D LONG", "D SHORT"]:
        if s in body:
            signal["setup"] = s.split()[0]
            signal["direction"] = s.split()[1]
            break
    for key, pattern in [("entry","Entry"), ("sl","SL"), ("tp","TP"), ("lots","Lots")]:
        m = re.search(r"{}:([\d.]+)".format(pattern), body)
        if m: signal[key] = float(m.group(1))
    return signal


def format_message(signal):
    now = datetime.utcnow().strftime("%d/%m %H:%M UTC")
    if signal["type"] == "CONFLUENCE":
        return "⚡ CONFIRMED BIAS — XAUUSD\n🕐 {}\nCLOSE D and re-enter as higher setup\nEntry: {}  SL: {}  TP: {}\nRisk upgraded ✅".format(now, signal["entry"], signal["sl"], signal["tp"])
    if signal["type"] == "REVERSAL":
        d = "LONG" if "LONG" in signal["raw"] else "SHORT"
        return "⚠️ REVERSAL WARNING — XAUUSD\n🕐 {}\n{} fired AGAINST open D trade\nCLOSE D NOW | Level: {}\nDecide manually whether to reverse".format(now, d, signal["entry"])
    if signal["type"] == "TP_HIT":
        setup = signal.get("setup","?")
        profit = PROFIT_MAP.get(setup, 250.0)
        record_trade(setup, signal.get("direction","?"), "WIN", profit)
        return "✅ TP HIT — XAUUSD\n🕐 {}\nSetup {} {}\nEntry: {} to TP: {}\nWINNER +${:.0f} 🎉".format(now, setup, signal.get("direction",""), signal["entry"], signal["tp"], profit)
    if signal["type"] == "SL_HIT":
        setup = signal.get("setup","?")
        loss = LOSS_MAP.get(setup, 100.0)
        record_trade(setup, signal.get("direction","?"), "LOSS", loss)
        return "❌ SL HIT — XAUUSD\n🕐 {}\nSetup {} {}\nEntry: {} to SL: {}\nLOSER -${:.0f}".format(now, setup, signal.get("direction",""), signal["entry"], signal["sl"], loss)
    emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
    arrow = "▲" if signal["direction"] == "LONG" else "▼"
    risk = {"B":"1.5%","C":"1.0%","D":"0.2%"}.get(signal["setup"],"")
    notes = ""
    if "T2 trail" in signal["raw"]: notes = "\n📋 T2 trail — move SL to BE at +1.5xATR"
    elif "2xSL" in signal["raw"]: notes = "\n📋 Fixed TP 2xSL"
    elif "2.5xATR" in signal["raw"]: notes = "\n📋 Fixed TP 2.5xATR"
    return "{} XAUUSD SIGNAL\n🕐 {}\nSetup {} — {} {}\nEntry: {}\nSL:    {}\nTP:    {}\nLots:  {}\n💰 Risk: {}{}".format(emoji, now, signal["setup"], signal["direction"], arrow, signal["entry"], signal["sl"], signal["tp"], signal["lots"], risk, notes)


@app.route("/", methods=["GET"])
def health():
    with stats_lock:
        w = weekly_stats["wins"]; l = weekly_stats["losses"]; p = weekly_stats["pnl"]
    return jsonify({"status": "ok", "service": "XAUUSD Webhook Bridge", "week_wins": w, "week_losses": l, "week_pnl": "${:+.0f}".format(p)}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        body = request.get_data(as_text=True).strip()
        log.info("Received: {}".format(body))
        signal = parse_signal(body)
        message = format_message(signal)
        sent = send_whatsapp(message)
        return jsonify({"status": "ok" if sent else "whatsapp_failed", "type": signal["type"], "setup": signal["setup"], "direction": signal["direction"], "entry": signal["entry"]}), 200
    except Exception as e:
        log.error("Error: {}".format(e))
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/summary", methods=["GET"])
def summary():
    msg = build_weekly_summary()
    sent = send_whatsapp(msg)
    return jsonify({"status": "ok" if sent else "failed", "message": msg}), 200


@app.route("/stats", methods=["GET"])
def stats():
    with stats_lock:
        return jsonify(weekly_stats), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
