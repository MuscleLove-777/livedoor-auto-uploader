# -*- coding: utf-8 -*-
"""
M国 content_pool ローダー（全uploader共通モジュール・ドロップイン可能）

dashboard/autonomy が毎日再生成する content_pool.json（レーン別タグ/コピー/CTA）を読む。
優先順: ①同リポ内 content_pool.json ②中央ハブ https://musclelove-777.github.io/content_pool.json
       ③空dict（呼び出し側の既存ハードコードへフォールバック = 絶対に死なない）

x_account_insights.json 互換の形式に変換する as_insights() を使えば、
既存uploaderは1-2行の変更で「毎日自動最適化」に接続できる（憲法第1条・第3条）。
"""
import json
import re
from pathlib import Path

HUB_URL = "https://musclelove-777.github.io/content_pool.json"
LOCAL_POOL = Path(__file__).resolve().parent / "content_pool.json"
HTTP_TIMEOUT = 10
URL_RE = re.compile(r"https?://[^\s)\]>]+")


def _with_utm(text: str, platform: str) -> str:
    """CTA内のURLへ utm_source=<platform>&utm_medium=autopost を付与する。
    GA4で「どの媒体の投稿が流入を生んだか」をreferrer喪失時も計測するための生命線。"""
    def repl(m):
        url = m.group(0)
        if "utm_source=" in url:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}utm_source={platform}&utm_medium=autopost"
    return URL_RE.sub(repl, text)


def load_pool(lane: str) -> dict:
    """レーン(safe_fitness / mature_muscle / adult_fanza)のプールを返す。失敗時は{}。"""
    data = None
    try:
        data = json.loads(LOCAL_POOL.read_text(encoding="utf-8"))
    except Exception:
        data = None
    if data is None:
        try:
            import requests
            r = requests.get(HUB_URL, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[pool_loader] pool unavailable (fallback to hardcoded): {e}")
            return {}
    if not isinstance(data, dict):
        return {}
    lane_data = (data.get("lanes") or {}).get(lane) or {}
    if not isinstance(lane_data, dict):
        return {}
    out = dict(lane_data)
    out["_generic_trend_candidates"] = data.get("generic_trend_candidates", [])
    out["_version"] = data.get("version", "")
    out["_goal_note"] = data.get("goal_note", "")
    return out


def as_insights(lane: str, platform: str = "") -> dict:
    """account_insights互換dictへ変換（recommended_tags/templates/ctas, avoid_tags）。
    platform指定時はCTA内URLへUTMを自動付与（utm_source=platform名）。"""
    pool = load_pool(lane)
    if not pool:
        return {}
    ins = {"updated_at_jst": f"content_pool v{pool.get('_version', '?')}"}

    tags = list(pool.get("base_tags", [])) + list(pool.get("trend_tags", []))
    generic = [g for g in pool.get("_generic_trend_candidates", []) if g]
    if generic:
        tags.append(generic[0])  # 汎用トレンドは最大1枠（憲法第3条）
    if tags:
        ins["recommended_tags"] = tags

    # pool側テンプレは {tags} プレースホルダ → uploader既存形式 {hashtags} に変換
    templates = []
    for cap in pool.get("caption_templates", []):
        t = str(cap).replace("{tags}", "{hashtags}").strip()
        if t:
            templates.append(t)
    if templates:
        ins["recommended_templates"] = templates

    ctas = [str(c).strip() for c in pool.get("cta_lines", []) if str(c).strip()]
    if platform:
        ctas = [_with_utm(c, platform) for c in ctas]
    if ctas:
        ins["recommended_ctas"] = ctas

    ng = [str(w).strip() for w in pool.get("ng_words", []) if str(w).strip()]
    if ng:
        ins["avoid_tags"] = ng
    return ins
