"""
XAUUSD Webhook Bridge - Railway.app
WhatsApp + Telegram alerts, weekly summary
"""
from flask import Flask, request, jsonify
import requests, os, re, logging, threading, time
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

WHATSAPP_NUMBER  = os.environ.get("WHATSAPP_NUMBER", "")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "")

PROFIT_MAP = {"B": 1875.0, "C": 1000.0, "D": 250.0}
LOSS_MAP   = {"B": 750.0, "C": 500.0, "D": 100.0}

weekly_stats = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": [], "week_start": datetime.utcnow().strftime("%d/%m")}
stats_lock = threading.Lock()
pending_signals = []
signals_lock = threading.Lock()
signal_counter = [0]

def reset_weekly_stats():
    with stats_lock:
        weekly_stats.update({"wins":0,"losses":0,"pnl":0.0,"trades":[],"week_start":datetime.utcnow().strftime("%d/%m")})

def record_trade(setup, direction, result, amount):
    with stats_lock:
        if result=="WIN": weekly_stats["wins"]+=1; weekly_stats["pnl"]+=amount
        else: weekly_stats["losses"]+=1; weekly_stats["pnl"]-=amount
        weekly_stats["trades"].append({"time":datetime.utcnow().strftime("%d/%m %H:%M"),"setup":setup,"direction":direction,"result":result,"amount":amount})

def build_weekly_summary():
    with stats_lock:
        wins=weekly_stats["wins"]; losses=weekly_stats["losses"]; pnl=weekly_stats["pnl"]
        total=wins+losses; wr=(wins/total*100) if total>0 else 0
        start=weekly_stats["week_start"]; trades=list(weekly_stats["trades"][-5:])
    now=datetime.utcnow().strftime("%d/%m")
    pnl_str="+${:.0f}".format(pnl) if pnl>=0 else "-${:.0f}".format(abs(pnl))
    tlog=""
    for t in trades: tlog+="\n  {} {} {} {}".format("WIN" if t["result"]=="WIN" else "LOSS",t["setup"],t["direction"],t["time"])
    return ("WEEKLY SUMMARY - Willys Gold Signals\nWeek: {} to {}\nTrades:{} Wins:{} Losses:{} WR:{:.1f}% P&L:{}\nLast 5:{}\nGood luck next week!").format(start,now,total,wins,losses,wr,pnl_str,tlog)

def weekly_scheduler():
    last=None
    while True:
        try:
            now=datetime.utcnow()
            if now.weekday()==6 and now.hour==7 and now.minute<5:
                wid=now.strftime("%Y-W%W")
                if wid!=last: send_all(build_weekly_summary()); last=wid; reset_weekly_stats()
            time.sleep(60)
        except Exception as e: log.error(str(e)); time.sleep(60)

threading.Thread(target=weekly_scheduler,daemon=True).start()
reset_weekly_stats()

def send_whatsapp(msg):
    if not WHATSAPP_NUMBER or not CALLMEBOT_APIKEY: return False
    try:
        r=requests.get("https://api.callmebot.com/whatsapp.php",params={"phone":WHATSAPP_NUMBER,"text":msg,"apikey":CALLMEBOT_APIKEY},timeout=10)
        return r.status_code==200
    except Exception as e: log.error("WA:{}".format(e)); return False

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHANNEL: return False
    try:
        r=requests.post("https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_TOKEN),json={"chat_id":TELEGRAM_CHANNEL,"text":msg},timeout=10)
        log.info("TG {} {}".format(r.status_code, r.text[:80]))
        return r.status_code==200
    except Exception as e: log.error("TG:{}".format(e)); return False

def send_all(msg):
    wa=send_whatsapp(msg); tg=send_telegram(msg); return wa or tg

def parse_signal(body):
    s={"raw":body,"setup":"?","direction":"?","entry":None,"sl":None,"tp":None,"lots":None,"type":"ENTRY"}
    if "TP HIT" in body: s["type"]="TP_HIT"
    elif "SL HIT" in body: s["type"]="SL_HIT"
    elif "CONFIRMED" in body: s["type"]="CONFLUENCE"
    elif "REVERSAL" in body: s["type"]="REVERSAL"
    for x in ["B LONG","B SHORT","C LONG","C SHORT","D LONG","D SHORT"]:
        if x in body: s["setup"]=x.split()[0]; s["direction"]=x.split()[1]; break
    for k,p in [("entry","Entry"),("sl","SL"),("tp","TP"),("lots","Lots")]:
        m=re.search(r"{}:([\d.]+)".format(p),body)
        if m: s[k]=float(m.group(1))
    return s

def format_message(s):
    now=datetime.utcnow().strftime("%d/%m %H:%M UTC")
    if s["type"]=="CONFLUENCE": return "CONFIRMED BIAS XAUUSD {} CLOSE D re-enter Entry:{} SL:{} TP:{}".format(now,s["entry"],s["sl"],s["tp"])
    if s["type"]=="REVERSAL":
        d="LONG" if "LONG" in s["raw"] else "SHORT"
        return "REVERSAL WARNING XAUUSD {} {} vs D CLOSE NOW Level:{}".format(now,d,s["entry"])
    if s["type"]=="TP_HIT":
        st=s.get("setup","?"); profit=PROFIT_MAP.get(st,250.0)
        record_trade(st,s.get("direction","?"),"WIN",profit)
        return "TP HIT XAUUSD {} Setup {} {} WINNER +${}".format(now,st,s.get("direction",""),int(profit))
    if s["type"]=="SL_HIT":
        st=s.get("setup","?"); loss=LOSS_MAP.get(st,100.0)
        record_trade(st,s.get("direction","?"),"LOSS",loss)
        return "SL HIT XAUUSD {} Setup {} {} LOSER -${}".format(now,st,s.get("direction",""),int(loss))
    e="LONG" if s["direction"]=="LONG" else "SHORT"
    r={"B":"1.5%","C":"1.0%","D":"0.2%"}.get(s["setup"],"")
    return "XAUUSD {} {} {} Entry:{} SL:{} TP:{} Lots:{} Risk:{}".format(s["setup"],e,now,s["entry"],s["sl"],s["tp"],s["lots"],r)

@app.route("/",methods=["GET"])
def health():
    with stats_lock: w=weekly_stats["wins"]; l=weekly_stats["losses"]; p=weekly_stats["pnl"]
    return jsonify({"status":"ok","service":"XAUUSD Webhook Bridge","week_wins":w,"week_losses":l,"week_pnl":"${:+.0f}".format(p)}),200

@app.route("/webhook",methods=["POST"])
def webhook():
    try:
        body=request.get_data(as_text=True).strip()
        sig=parse_signal(body); msg=format_message(sig)
        sent=False
        if sig["type"] not in ("TP_HIT","SL_HIT"): sent=send_all(msg)
        if sig["type"]=="ENTRY" and sig["entry"]:
            signal_counter[0]+=1
            m={"id":str(signal_counter[0]),"setup":sig["setup"],"direction":sig["direction"],"entry":sig["entry"],"sl":sig["sl"],"tp":sig["tp"],"lots":sig["lots"] or 0.01,"time":datetime.utcnow().strftime("%Y%m%d%H%M%S")}
            with signals_lock:
                pending_signals.append(m)
                if len(pending_signals)>5: pending_signals.pop(0)
        return jsonify({"status":"ok" if sent else "failed","type":sig["type"],"setup":sig["setup"]}),200
    except Exception as e: log.error(str(e)); return jsonify({"status":"error","message":str(e)}),500

@app.route("/pending_signal",methods=["GET"])
def pending_signal():
    with signals_lock:
        if pending_signals: return jsonify({"signal":pending_signals.pop(0)}),200
    return jsonify({"signal":None}),200

@app.route("/trade_result",methods=["POST"])
def trade_result():
    try:
        d=request.get_json()
        setup=d.get("setup","?"); direction=d.get("direction","?"); result=d.get("result","LOSS"); pnl=float(d.get("pnl",0))
        record_trade(setup,direction,result,abs(pnl))
        icon="✅" if pnl>=0 else "❌"
        pnl_str=("+$" if pnl>=0 else "-$")+"{:.2f}".format(abs(pnl))
        entry_str="{:.2f}".format(float(d["entry"])) if d.get("entry") else "N/A"
        tp_sl="TP HIT" if result=="WIN" else "SL HIT"
        msg="{} MT5 CLOSED — Setup {} {}\nResult: {}\nEntry: {}\nP&L: {}".format(icon,setup,direction,tp_sl,entry_str,pnl_str)
        send_all(msg)
        return jsonify({"status":"ok"}),200
    except: return jsonify({"status":"error"}),500

@app.route("/broadcast",methods=["POST"])
def broadcast():
    try:
        d=request.get_json(); msg=d.get("message","")
        if not msg: return jsonify({"status":"error"}),400
        sent=send_all(msg); return jsonify({"status":"ok" if sent else "failed"}),200
    except: return jsonify({"status":"error"}),500

@app.route("/summary",methods=["GET"])
def summary():
    msg=build_weekly_summary(); sent=send_all(msg); return jsonify({"status":"ok" if sent else "failed","message":msg}),200

@app.route("/send_intro", methods=["GET"])
def send_intro():
    """One-time send of intro message to all channels"""
    msg = """XAUUSD Signal System

Automated gold trading signals during London session, Mon-Fri, 3pm-7pm Perth time.

You will see 2 messages per trade:

- Entry alert with setup, entry price, SL, TP, and lots

- Close result from MT5 with real broker P&L (✅ win or ❌ loss)

Setup types:

B = 5-day breakout (rare, 1.5% risk)

C = London reversal (1% risk)

D = Intraday trend (most frequent, 0.2% risk)

CONFIRMED BIAS = strong same-direction signal

REVERSAL = close current trade

How to follow: Don't copy lot sizes directly, they're set for my account. Ask me for sizing help.

16-year backtest (2009-2025) on $50k account:

Profitable every single year. Best year +$78k (2010). Worst year +$30k (2023). Total +$849k across 8,972 trades. Win rate 55%. Max drawdown -2.5%. Worst single day -$1,263.

Reality check: Past performance is not future results. Some days will lose. Long-term edge is the goal."""
    sent = send_all(msg)
    return jsonify({"status": "ok" if sent else "failed", "preview": msg[:200]}), 200
@app.route("/stats",methods=["GET"])
def stats():
    with stats_lock: return jsonify(weekly_stats),200

if __name__=="__main__":
    port=int(os.environ.get("PORT",8080)); app.run(host="0.0.0.0",port=port)
