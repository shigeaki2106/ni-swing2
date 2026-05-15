"""
日本株 監視ユニバース (Core Universe for Japan)

クラウド自動化(notify_daily.py)で毎朝チェックする日本株のリスト。
~80銘柄程度。楽天 SPF の範囲をカバーするため、
時価総額100億〜2000億円帯のグロース・スイング候補を中心に選定。

セクター分散:
  半導体・電子部品 / ソフトウェア・SaaS / 産業機械 / 消費 / 金融・商社 / ETF
"""

# ──────────────────────────────────────────────────
# 半導体・電子部品(ボラ高、モメンタム特性)
# ──────────────────────────────────────────────────
TIER_SEMICON = [
    '6526.T',  # ソシオネクスト
    '6920.T',  # レーザーテック
    '6857.T',  # アドバンテスト
    '6754.T',  # アンリツ
    '6963.T',  # ローム
    '7735.T',  # SCREENホールディングス
    '6890.T',  # フェローテック
    '6324.T',  # ハーモニック・ドライブ・システムズ
    '6707.T',  # サンケン電気
    '6967.T',  # 新光電気工業
    '6981.T',  # 村田製作所
    '6594.T',  # ニデック
]

# ──────────────────────────────────────────────────
# ソフトウェア・SaaS・グロース
# ──────────────────────────────────────────────────
TIER_SAAS = [
    '3923.T',  # ラクス
    '3994.T',  # マネーフォワード
    '4385.T',  # メルカリ
    '4475.T',  # HENNGE
    '4477.T',  # BASE
    '4480.T',  # メドレー
    '4488.T',  # AIinside
    '4483.T',  # JMDC
    '4493.T',  # サイバーセキュリティクラウド
    '4498.T',  # サイバートラスト
    '6532.T',  # ベイカレント・コンサルティング
    '7177.T',  # GMOフィナンシャルゲート
    '7095.T',  # Macbee Planet
    '4751.T',  # サイバーエージェント
    '4716.T',  # 日本オラクル
]

# ──────────────────────────────────────────────────
# 産業機械・自動車関連(中型グロース)
# ──────────────────────────────────────────────────
TIER_INDUSTRIAL = [
    '1960.T',  # サンテック(現在のwatchlist)
    '7721.T',  # 東京計器(現在のwatchlist)
    '6118.T',  # アイダエンジニアリング
    '6135.T',  # 牧野フライス製作所
    '6256.T',  # ニューフレアテクノロジー
    '6294.T',  # オカダアイヨン
    '6273.T',  # SMC
    '7011.T',  # 三菱重工業
    '7012.T',  # 川崎重工業
    '7741.T',  # HOYA
    '6645.T',  # オムロン
    '6701.T',  # NEC
]

# ──────────────────────────────────────────────────
# 消費・小売・サービス(モメンタム反応しやすい中型)
# ──────────────────────────────────────────────────
TIER_CONSUMER = [
    '3092.T',  # ZOZO
    '3382.T',  # セブン&アイ
    '2925.T',  # ピックルスホールディングス
    '4661.T',  # オリエンタルランド
    '9843.T',  # ニトリホールディングス
    '7974.T',  # 任天堂
    '9983.T',  # ファーストリテイリング
    '9202.T',  # ANAホールディングス
    '7780.T',  # メニコン
    '9229.T',  # サンウェルズ
]

# ──────────────────────────────────────────────────
# 医薬品・医療機器
# ──────────────────────────────────────────────────
TIER_PHARMA = [
    '4502.T',  # 武田薬品
    '4519.T',  # 中外製薬
    '4528.T',  # 小野薬品工業
    '4523.T',  # エーザイ
    '4543.T',  # テルモ
    '7733.T',  # オリンパス
    '4592.T',  # サンバイオ
]

# ──────────────────────────────────────────────────
# 金融・商社・通信(マクロ動向で動く)
# ──────────────────────────────────────────────────
TIER_FIN = [
    '8001.T',  # 伊藤忠商事
    '8058.T',  # 三菱商事
    '8031.T',  # 三井物産
    '8002.T',  # 丸紅
    '8473.T',  # SBIホールディングス
    '8628.T',  # 松井証券
    '9433.T',  # KDDI
    '9434.T',  # ソフトバンク
    '9984.T',  # ソフトバンクグループ
    '6098.T',  # リクルートHD
]

# ──────────────────────────────────────────────────
# ETF(指数・レバレッジ)
# ──────────────────────────────────────────────────
TIER_ETF = [
    '1321.T',  # NEXT FUNDS 日経225連動型
    '1306.T',  # NEXT FUNDS TOPIX連動型
    '1570.T',  # 日経225レバレッジ
    '1357.T',  # 日経225ベア
    '1568.T',  # TOPIX 2倍
    '1545.T',  # ナスダック100連動型
]

# ──────────────────────────────────────────────────
# 統合
# ──────────────────────────────────────────────────
CORE_UNIVERSE = (
    TIER_SEMICON
    + TIER_SAAS
    + TIER_INDUSTRIAL
    + TIER_CONSUMER
    + TIER_PHARMA
    + TIER_FIN
    + TIER_ETF
)

# 重複除去
CORE_UNIVERSE = list(dict.fromkeys(CORE_UNIVERSE))


def get_universe(include_watchlist=True, script_dir=None):
    """ユニバースを取得。デフォルトではwatchlistの銘柄も追加。

    Args:
        include_watchlist: watchlist.json の銘柄も含めるか
        script_dir: watchlist.json のあるディレクトリ(Noneなら自身のディレクトリ)
    """
    universe = list(CORE_UNIVERSE)

    if include_watchlist:
        import os
        import json
        if script_dir is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
        wl_path = os.path.join(script_dir, 'watchlist.json')
        if os.path.exists(wl_path):
            try:
                with open(wl_path, 'r', encoding='utf-8') as f:
                    wl = json.load(f)
                # _meta 以外のキーが銘柄コード
                for code in wl.keys():
                    if code != '_meta':
                        ticker = f"{code}.T" if not code.endswith('.T') else code
                        if ticker not in universe:
                            universe.append(ticker)
            except Exception:
                pass

    return universe


def describe():
    print(f"  日本株 監視ユニバース: 全{len(CORE_UNIVERSE)}銘柄")
    print(f"    半導体・電子部品:   {len(TIER_SEMICON):>2}銘柄")
    print(f"    SaaS・グロース:    {len(TIER_SAAS):>2}銘柄")
    print(f"    産業機械:          {len(TIER_INDUSTRIAL):>2}銘柄")
    print(f"    消費・小売:        {len(TIER_CONSUMER):>2}銘柄")
    print(f"    医薬・医療:        {len(TIER_PHARMA):>2}銘柄")
    print(f"    金融・商社・通信:   {len(TIER_FIN):>2}銘柄")
    print(f"    ETF:              {len(TIER_ETF):>2}銘柄")


if __name__ == '__main__':
    describe()
    print()
    print("  ユニバース全銘柄:")
    universe = get_universe(include_watchlist=True)
    for t in universe:
        print(f"    {t}")
