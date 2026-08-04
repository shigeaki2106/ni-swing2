# -*- coding: utf-8 -*-
"""
市場の広がり(Breadth)の算出 — 地合い判定のB層

TOPIX500(Core30 + Large70 + Mid400 = 約493銘柄)のうち、
「株価が200日移動平均を上回っている銘柄の割合(%)」を計算する。

指数(日経平均)は値がさ株の影響を受けるが、Breadthは中身が伴っているかを見る。
  70%以上 = 中身も強い / 30〜70% = まちまち / 30%未満 = 大半が下降トレンド

単体実行:  python breadth.py
"""
import os
import json
import random
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
UNIV_JSON = os.path.join(HERE, "topix500.json")   # 構成銘柄リスト(キャッシュ)
DATA_J = os.path.join(HERE, "data_j.xls")         # JPX上場銘柄一覧(規模区分入り)

WORKERS = 8          # Yahooへの同時接続数(マナーの範囲で並列化。493銘柄を約1分で処理)
MIN_COVERAGE = 0.60  # 6割以上の銘柄が取れなければBreadthは「判定不能」とする


# ------------------------------------------------------------
# 構成銘柄リスト
# ------------------------------------------------------------
def build_universe():
    """JPXの上場銘柄一覧(data_j.xls)から TOPIX500 の銘柄コードを抜き出してキャッシュする。
    data_j.xlsが無い環境(クラウド等)ではキャッシュ済みのtopix500.jsonをそのまま使う。"""
    if os.path.exists(UNIV_JSON):
        with open(UNIV_JSON, encoding="utf-8") as f:
            codes = json.load(f).get("codes", [])
        if codes:
            return codes
    if not os.path.exists(DATA_J):
        return []
    import pandas as pd
    df = pd.read_excel(DATA_J)
    df.columns = ["date", "code", "name", "market", "c33", "n33", "c17", "n17", "sc", "scale"]
    t500 = df[df["scale"].astype(str).str.contains("Core30|Large70|Mid400", na=False)]
    codes = [str(c).strip() for c in t500["code"].tolist()]
    with open(UNIV_JSON, "w", encoding="utf-8") as f:
        json.dump({"codes": codes, "note": "TOPIX500 (Core30+Large70+Mid400)"}, f, ensure_ascii=False)
    return codes


# ------------------------------------------------------------
# 1銘柄ぶんの判定
# ------------------------------------------------------------
def _above_ma(code):
    """その銘柄が200日線/50日線を上回っていれば True。取得失敗は None。
    戻り値: (above200, above50) それぞれ True/False、失敗時は None"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.T?range=1y&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        time.sleep(random.uniform(0, 0.25))  # アクセスの山を崩す(レート制限よけ)
        with urllib.request.urlopen(req, timeout=20) as res:
            data = json.load(res)
        closes = [c for c in data["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) < 200:
            return None
        price = closes[-1]
        ma200 = sum(closes[-200:]) / 200
        ma50 = sum(closes[-50:]) / 50
        return (price > ma200, price > ma50)
    except Exception:
        return None


# ------------------------------------------------------------
# Breadth本体
# ------------------------------------------------------------
def fetch_breadth(codes=None):
    """TOPIX500の200日線超え比率(%)を返す。取得できなければ None。
    戻り値: {"pct": 61.2, "pct50": 55.0, "n": 480, "total": 493, "verdict": "mid", ...}"""
    codes = codes or build_universe()
    if not codes:
        return None
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(_above_ma, codes))
    ok = [r for r in results if r is not None]
    if len(ok) < len(codes) * MIN_COVERAGE:
        return None  # 取得が少なすぎる=信用できないのでBreadthは使わない
    above200 = sum(1 for a200, _ in ok if a200)
    above50 = sum(1 for _, a50 in ok if a50)
    pct = round(above200 / len(ok) * 100, 1)
    pct50 = round(above50 / len(ok) * 100, 1)
    # 一般的な閾値(70/30)。強気=中身も強い / 弱気=大半が下降トレンド
    if pct >= 70:
        verdict, label = "bull", "良好"
    elif pct >= 30:
        verdict, label = "mid", "まちまち"
    else:
        verdict, label = "bear", "総崩れ"
    return {
        "pct": pct, "pct50": pct50, "n": len(ok), "total": len(codes),
        "verdict": verdict, "label": label,
    }


if __name__ == "__main__":
    codes = build_universe()
    print(f"universe: {len(codes)} stocks")
    t0 = time.time()
    b = fetch_breadth(codes)
    print(f"elapsed: {time.time() - t0:.1f}s")
    print(json.dumps(b, ensure_ascii=False))
