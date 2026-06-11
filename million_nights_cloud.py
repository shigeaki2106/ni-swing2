# -*- coding: utf-8 -*-
"""
MILLION NIGHTS クラウド版 (GitHub Actions用・標準ライブラリのみ)
PCが完全に切れていても、毎朝Discordに通知と一括審査の作戦カードを届ける。

モード (環境変数 MN_MODE、未指定ならUTC時刻から自動判定):
  wake    : 6:00 JST  起床通知(地合い付き)
  card    : 7:00 JST  universe.csv を一括審査して作戦カードを配信
  evening : 21:30 JST 夜の日誌リマインド

審査基準はローカル版(SWING_COMMAND.html の autoJudgeStock / stockdata_fetch.py)と
同一。基準を変更するときは3箇所すべて更新すること。
"""
import os
import sys
import csv
import json
import math
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
UNIVERSE_CSV = os.path.join(HERE, "universe.csv")

CAPITAL = 500000   # 運用資金
RISK_PCT = 2.0     # 1トレードの許容損失(%)

WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


def jst_now():
    return datetime.now(timezone.utc) + timedelta(hours=9)


# ------------------------------------------------------------
# Discord
# ------------------------------------------------------------
def send_discord(text):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("ERROR: DISCORD_WEBHOOK_URL が未設定")
        sys.exit(1)
    payload = json.dumps({"content": text[:1990]}).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "MillionNights/1.0"},
    )
    urllib.request.urlopen(req, timeout=15)


# ------------------------------------------------------------
# Yahoo Finance
# ------------------------------------------------------------
def _yahoo(symbol, rng):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)["chart"]["result"][0]


def fetch_nikkei():
    """日経平均の地合い (ローカル版 millionnights_notify.py と同基準)"""
    data = _yahoo("%5EN225", "3mo")
    closes = [c for c in data["indicators"]["quote"][0]["close"] if c is not None]
    close = closes[-1]
    ma25 = sum(closes[-25:]) / 25
    ma25_old = sum(closes[-35:-10]) / 25
    if close > ma25 and ma25 > ma25_old:
        return {"icon": "🟢", "label": "強気", "verdict": "bull", "close": close, "ma25": ma25, "rising": True}
    if close > ma25:
        return {"icon": "🟡", "label": "中立", "verdict": "mid", "close": close, "ma25": ma25, "rising": False}
    return {"icon": "🔴", "label": "弱気", "verdict": "bear", "close": close, "ma25": ma25, "rising": False}


def sma(vals, n, offset=0):
    end = len(vals) - offset
    if end - n < 0:
        return None
    return sum(vals[end - n:end]) / n


def analyze(code):
    """個別銘柄: 配当調整済みで各種指標を計算 (stockdata_fetch.pyと同一ロジック)"""
    result = _yahoo(f"{code}.T", "2y")
    q = result["indicators"]["quote"][0]
    adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose")
    rows = []
    for i, (o, h, l, c, v) in enumerate(zip(q["open"], q["high"], q["low"], q["close"], q["volume"])):
        if c is None or o is None:
            continue
        f = (adj[i] / c) if (adj and adj[i] is not None and c) else 1.0
        rows.append((o * f, h * f, l * f, c * f, v or 0))
    if len(rows) < 240:
        raise ValueError(f"データ不足({len(rows)}日)")
    opens = [r[0] for r in rows]; highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]; closes = [r[3] for r in rows]; vols = [r[4] for r in rows]
    price = closes[-1]
    ma5, ma5p = sma(closes, 5), sma(closes, 5, 1)
    ma25, ma50, ma150, ma200 = sma(closes, 25), sma(closes, 50), sma(closes, 150), sma(closes, 200)
    ma200_30 = sma(closes, 200, 30)
    up_v, dn_v = [], []
    for i in range(len(rows) - 20, len(rows)):
        (up_v if closes[i] >= opens[i] else dn_v).append(vols[i])
    vol_ratio = (sum(up_v) / len(up_v)) / (sum(dn_v) / len(dn_v)) if up_v and dn_v else 9.9
    d5 = (ma5 - ma5p) / ma5p * 100
    o, c = opens[-1], closes[-1]
    rng = [(h - l) / cl for h, l, cl in zip(highs, lows, closes)]
    r10, r30 = sum(rng[-10:]) / 10, sum(rng[-40:-10]) / 30
    v10, v30 = sum(vols[-10:]) / 10, sum(vols[-40:-10]) / 30
    return {
        "price": price, "ma25": ma25, "ma50": ma50, "ma150": ma150, "ma200": ma200,
        "ma200_growth": (ma200 - ma200_30) / ma200_30 * 100 if ma200_30 else 0,
        "hi52": max(highs[-252:]), "lo52": min(lows[-252:]), "hi3m": max(highs[-63:]),
        "gap": (opens[-1] - closes[-2]) / closes[-2] * 100,
        "vol_ratio": vol_ratio,
        "ma5_dir": "up" if d5 > 0.05 else ("down" if d5 < -0.05 else "flat"),
        "kahanshin": bool(c > o and (c + o) / 2 > ma5 and o <= ma5 * 1.005),
        "vcp": "yes" if (r10 <= r30 * 0.75 and v10 <= v30 * 0.95) else ("wild" if r10 >= r30 * 1.3 else "dunno"),
    }


# ------------------------------------------------------------
# ユニバースCSVと審査
# ------------------------------------------------------------
def _num(s):
    try:
        return float(str(s).replace(",", "").replace('"', "").strip())
    except (ValueError, TypeError):
        return None


def read_universe():
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(UNIVERSE_CSV, encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("universe.csv の文字コード判別失敗")
    head = rows[0]
    def idx(key):
        for i, h in enumerate(head):
            if key in h:
                return i
        return -1
    i_code, i_name = idx("コード"), idx("銘柄名")
    i_rev, i_prof, i_eq, i_cap = idx("売上高変化率"), idx("経常利益変化率"), idx("自己資本比率"), idx("時価総額")
    out = []
    for r in rows[1:]:
        if len(r) <= max(i_code, i_name) or not r[i_code].strip():
            continue
        get = lambda i: _num(r[i]) if 0 <= i < len(r) else None
        out.append({"code": r[i_code].strip(), "name": r[i_name].strip(),
                    "rev": get(i_rev), "prof": get(i_prof), "eq": get(i_eq), "cap": get(i_cap)})
    return out


def step1_fails(row):
    """財務関門 (クラウド版は当日騰落の代わりにQ10ギャップで判定するためchgなし)"""
    f = []
    if row["rev"] is None or row["rev"] < 20: f.append("売上+20%未満")
    if row["prof"] is None or row["prof"] < 20: f.append("経常+20%未満")
    if row["eq"] is None or row["eq"] < 50: f.append("自己資本50%未満")
    if row["cap"] is None or not (10000 <= row["cap"] <= 150000): f.append("時価総額レンジ外")
    return f


def auto_judge(s):
    fails, warns, score = [], [], 0
    price = s["price"]
    if not (price > s["ma50"] > s["ma150"] > s["ma200"]):
        fails.append("パーフェクトオーダー不成立")
    else:
        from_low = (price - s["lo52"]) / s["lo52"] * 100
        from_high = (price - s["hi52"]) / s["hi52"] * 100
        if from_low < 30: fails.append("52週安値から+30%未満")
        elif from_high < -25: fails.append("52週高値から25%超下")
        else:
            m = (price - s["ma200"]) / s["ma200"] * 100
            score += 25 if m >= 15 else 20 if m >= 10 else 15 if m >= 5 else 10
    g = s["ma200_growth"]
    if g <= 0: fails.append("200日線が上向きでない")
    else: score += 15 if g >= 3 else 8
    vr = s["vol_ratio"]
    if vr < 0.8: fails.append("下落日の出来高が多い")
    else:
        score += 20 if vr >= 1.5 else 13 if vr >= 1.3 else 5
        if vr < 1.3: warns.append("出来高横ばい")
    if s["vcp"] == "yes": score += 5
    elif s["vcp"] == "wild": warns.append("乱高下中")
    d = (price - s["ma25"]) / s["ma25"] * 100
    if d > 15: fails.append("25日線から+15%超の過熱")
    else:
        score += 10 if d < 0 else 25 if d <= 2 else 20 if d <= 5 else 13 if d <= 8 else 7
        if d > 8: warns.append(f"乖離+{d:.0f}%")
        if d < 0: warns.append("25日線の下")
    gap = abs(s["gap"])
    if gap > 5: fails.append("当日±5%超の急変動")
    else: score += 15 if gap <= 0.5 else 12 if gap <= 1 else 9 if gap <= 2 else 6 if gap <= 3 else 3
    kah = False
    if s["ma5_dir"] == "down": fails.append("5日線が下向き")
    else:
        if s["ma5_dir"] == "flat": warns.append("5日線横ばい")
        if s["kahanshin"] and s["ma5_dir"] == "up":
            score += 5; kah = True
    score = min(100, score)
    grade = "S" if score >= 80 else "A" if score >= 65 else "B" if score >= 50 else "C"
    return fails, warns, score, grade, kah


def trade_plan(s):
    trigger = math.ceil(s["hi3m"] * 1.005)
    stop = math.floor(trigger * 0.95)
    risk_ps = trigger - stop
    shares = int(CAPITAL * RISK_PCT / 100 // risk_ps) if risk_ps > 0 else 0
    return trigger, stop, min(shares, int(CAPITAL // trigger))


# ------------------------------------------------------------
# 各モード
# ------------------------------------------------------------
def mode_wake():
    d = jst_now()
    try:
        n = fetch_nikkei()
        line = f"📊 今朝の地合い: {n['icon']} **{n['label']}** (終値 {n['close']:,.0f}円 / 25日線 {n['ma25']:,.0f}円)"
        if n["verdict"] == "bear":
            line += "\n⛔ **弱気のため今日は全銘柄見送りでOK。二度寝推奨。**"
    except Exception:
        line = "📊 地合いの取得に失敗 — 7:00の作戦カードで確認してください"
    send_discord(
        f"🌃 **MILLION NIGHTS — おはようございます!** ({d.month}/{d.day} {WEEKDAYS[d.weekday()]}曜)\n"
        f"{line}\n"
        "⏰ 7:00に一括審査の**作戦カード**がここに届きます(PCオフでもOK)。\n"
        "_銘柄リストを最新にしたい日だけ、楽天CSVを保存してPCを起動してください。_"
    )


def mode_card():
    d = jst_now()
    rows = read_universe()
    uni_date = datetime.fromtimestamp(os.path.getmtime(UNIVERSE_CSV)).strftime("%m/%d") if os.path.exists(UNIVERSE_CSV) else "?"
    fin_passed = [r for r in rows if not step1_fails(r)]
    print(f"ユニバース{len(rows)}銘柄 / 財務通過{len(fin_passed)}")
    cands, rejected, errors = [], [], 0
    for r in fin_passed:
        try:
            s = analyze(r["code"])
        except Exception as e:
            print(f"  [NG] {r['code']} {e}")
            errors += 1
            continue
        fails, warns, score, grade, kah = auto_judge(s)
        if fails:
            rejected.append((r, fails))
            print(f"  [見送り] {r['code']} {fails[0]}")
        else:
            cands.append((r, s, warns, score, grade, kah))
            print(f"  [候補] {r['code']} {grade}{score}")
        time.sleep(0.35)
    cands.sort(key=lambda x: -x[3])

    lines = [f"🛰 **MILLION NIGHTS 本日の作戦カード** ({d.month}/{d.day} {WEEKDAYS[d.weekday()]}曜) ☁クラウド審査",
             f"ユニバース({uni_date}時点) {len(rows)}銘柄 → 財務通過{len(fin_passed)} → 🏆候補 **{len(cands)}**"]
    if not cands:
        lines.append("候補なし。**今日は静観でOK。** 「何もしないことは、立派な利益確定です。」")
    medals = ["🥇", "🥈", "🥉"]
    for i, (r, s, warns, score, grade, kah) in enumerate(cands[:3]):
        trigger, stop, shares = trade_plan(s)
        lines.append(f"{medals[i] if i < 3 else '・'} **{r['code']} {r['name']}**  {grade}級 {score}点"
                     + (" 🦵下半身成立!" if kah else ""))
        lines.append(f"　🎯トリガー {trigger:,}円 / 🛡損切り {stop:,}円 / 2%ルール {shares}株(かぶミニ)")
        if warns:
            lines.append(f"　⚠ {' / '.join(warns[:3])}")
    if rejected:
        rej = " / ".join(f"{r['code']}({f[0]})" for r, f in rejected[:5])
        lines.append(f"⛔ 見送り {len(rejected)}件: {rej}" + (" ほか" if len(rejected) > 5 else ""))
    if errors:
        lines.append(f"(取得失敗 {errors}件)")
    if cands:
        lines.append("✅ 残す確認は **Q8ニュース・Q9決算日** — iSPEEDで各30秒 → OKなら注文+逆指値")
    send_discord("\n".join(lines))


def mode_evening():
    d = jst_now()
    send_discord(
        f"🌙 **MILLION NIGHTS — 夜の振り返りタイム** ({d.month}/{d.day} {WEEKDAYS[d.weekday()]}曜)\n"
        "4️⃣ 保有銘柄の終値チェック (損切り・利確・タイムストップ?)\n"
        "5️⃣ 📓航海日誌を更新 (約定・気持ち・学び)\n"
        "_負けた日は反省を書くまでが今日のミッション。未反省2件で新規ロックです📝_"
    )


def main():
    mode = os.environ.get("MN_MODE", "").strip()
    if not mode:
        h = datetime.now(timezone.utc).hour
        mode = "wake" if h == 21 else "card" if h == 22 else "evening" if h == 12 else "card"
    print(f"=== MILLION NIGHTS cloud [{mode}] {jst_now().isoformat()} JST ===")
    {"wake": mode_wake, "card": mode_card, "evening": mode_evening}[mode]()
    print("完了")


if __name__ == "__main__":
    main()
