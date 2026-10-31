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
    arrow = "\U0001f7e2 \u0643\u0648\u0644 (Call) \u25b2" if side == "call" else "\U0001f534 \u0628\u0648\u062a (Put) \u25bc"
    status = "\u062a\u0645 \u0627\u0644\u0627\u062e\u062a\u0631\u0627\u0642" if side == "call" else "\u062a\u0645 \u0627\u0644\u0643\u0633\u0631"
    return (
        f"{arrow} \u2014 {sym}\n"
        f"\u0627\u0644\u0633\u0639\u0631 \u0627\u0644\u062d\u0627\u0644\u064a \u0644\u0644\u0633\u0647\u0645: {price:.2f}$\n"
        f"\u0627\u0644\u0633\u062a\u0631\u0627\u064a\u0643: {strike:g}$\n"
        f"\u062a\u0627\u0631\u064a\u062e \u0627\u0644\u0627\u0646\u062a\u0647\u0627\u0621: {exp}\n"
        f"\u0633\u0639\u0631 \u0627\u0644\u0639\u0642\u062f: {premium:.2f}$\n"
        f"\u062a\u0643\u0644\u0641\u0629 \u0627\u0644\u0639\u0642\u062f: {premium * 100:.0f}$\n"
        f"\u0627\u0644\u062f\u062e\u0648\u0644: {entry:.2f}$\n"
        f"\u0627\u0644\u0647\u062f\u0641: {t1:.2f}$ \u2013 {t2:.2f}$\n"
        f"\u0648\u0642\u0641 \u0627\u0644\u062e\u0633\u0627\u0631\u0629: {stop:.2f}$\n"
        f"\u062d\u0627\u0644\u0629 \u0627\u0644\u0625\u0634\u0627\u0631\u0629: {status}\n"
        "\u26a0\ufe0f \u0644\u064a\u0633\u062a \u062a\u0648\u0635\u064a\u0629 \u0646\u0647\u0627\u0626\u064a\u0629\u060c \u0627\u0644\u062a\u0632\u0645 \u0628\u0625\u062f\u0627\u0631\u0629 \u0631\u0623\u0633 \u0627\u0644\u0645\u0627\u0644 \u0648\u0648\u0642\u0641 \u0627\u0644\u062e\u0633\u0627\u0631\u0629."
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
                print(sym, "\u062e\u0637\u0623:", err)
    save_state(st)


if __name__ == "__main__":
    main()
