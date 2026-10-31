‎"""يشتغل مرة وحدة كل استدعاء (لـ GitHub Actions) ويحفظ حالته في state.json"""
import os
import json
import datetime as dt
import zoneinfo
import requests
import yfinance as yf

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
WATCHLIST = ["TSLA", "MSFT", "AAPL", "NVDA", "AMZN", "META"]
TZ = zoneinfo.ZoneInfo("Asia/Riyadh")
STATE_FILE = "state.json"

‎# (بداية، نهاية، حد الكول، حد البوت) بتوقيت الرياض
WINDOWS = [
    ((3, 0), (8, 0), 25, 25),
    ((8, 0), (12, 0), 15, 15),
    ((12, 0), (16, 0), 10, 0),
    ((16, 30), (20, 0), 5, 5),
    ((20, 0), (23, 0), 3, 3),
]


def load_state(today):
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
        if st.get("date") == today:
            return st
    except Exception:
        pass
    return {"date": today, "counts": {}, "seen": []}


def save_state(st):
    with open(STATE_FILE, "w") as f:
        json.dump(st, f)


def current_window(now):
    t = (now.hour, now.minute)
    for i, (s, e, c, p) in enumerate(WINDOWS):
        if s <= t < e:
            return i, {"call": c, "put": p}
    return None, None


def analyze(sym):
    df = yf.Ticker(sym).history(period="5d", interval="5m", prepost=True)
    if len(df) < 40:
        return None
    c = df["Close"]
    ema9 = c.ewm(span=9).mean().iloc[-1]
    ema21 = c.ewm(span=21).mean().iloc[-1]
    d = c.diff()
    up = d.clip(lower=0).rolling(14).mean()
    dn = (-d.clip(upper=0)).rolling(14).mean()
    rsi = (100 - 100 / (1 + up / dn)).iloc[-1]
    atr = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
    res = df["High"].iloc[-21:-1].max()
    sup = df["Low"].iloc[-21:-1].min()
    price = c.iloc[-1]
    if price > res and ema9 > ema21 and 50 < rsi < 75:
        return "call", price, res, price + 1.5 * atr, price + 2.5 * atr, price - atr
    if price < sup and ema9 < ema21 and 25 < rsi < 50:
        return "put", price, sup, price - 1.5 * atr, price - 2.5 * atr, price + atr
    return None


def pick_contract(sym, side, price):
    t = yf.Ticker(sym)
    today = dt.date.today()
    exps = [e for e in t.options if (dt.date.fromisoformat(e) - today).days >= 5]
    if not exps:
        return None
    tbl = t.option_chain(exps[0])
    tbl = tbl.calls if side == "call" else tbl.puts
    tbl = tbl[tbl["lastPrice"] > 0]
    if tbl.empty:
        return None
    want = price * (1.01 if side == "call" else 0.99)
    row = tbl.iloc[(tbl["strike"] - want).abs().argsort()[:1]].iloc[0]
    return exps[0], float(row["strike"]), float(row["lastPrice"])


def send(text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=15,
    )


def build_message(sym, side, price, entry, t1, t2, stop, contract):
    exp, strike, premium = contract
    arrow = "🟢 كول (Call) ▲" if side == "call" else "🔴 بوت (Put) ▼"
    status = "تم الاختراق" if side == "call" else "تم الكسر"
    return (
        f"{arrow} — {sym}\n"
        f"السعر الحالي للسهم: {price:.2f}$\n"
        f"السترايك: {strike:g}$\n"
        f"تاريخ الانتهاء: {exp}\n"
        f"سعر العقد: {premium:.2f}$\n"
        f"تكلفة العقد: {premium * 100:.0f}$\n"
        f"الدخول: {entry:.2f}$\n"
        f"الهدف: {t1:.2f}$ – {t2:.2f}$\n"
        f"وقف الخسارة: {stop:.2f}$\n"
        f"حالة الإشارة: {status}\n"
‎        "⚠️ ليست توصية نهائية، التزم بإدارة رأس المال ووقف الخسارة."
    )


def main():
    now = dt.datetime.now(TZ)
    st = load_state(str(now.date()))
    idx, limits = current_window(now)
    if idx is not None:
        used = st["counts"].setdefault(str(idx), {"call": 0, "put": 0})
        for sym in WATCHLIST:
            try:
                sig = analyze(sym)
                if not sig:
                    continue
                side = sig[0]
                key = f"{sym}|{side}"
                if key in st["seen"] or used[side] >= limits[side]:
                    continue
                contract = pick_contract(sym, side, sig[1])
                if not contract:
                    continue
                send(build_message(sym, *sig, contract))
                st["seen"].append(key)
                used[side] += 1
            except Exception as err:
                print(sym, "خطأ:", err)
    save_state(st)


if __name__ == "__main__":
    main()
