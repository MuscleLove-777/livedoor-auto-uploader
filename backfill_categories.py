# -*- coding: utf-8 -*-
"""公開済み記事にカテゴリを遡って付ける一括スクリプト。

背景:
  投稿処理が post_article(category=None) を渡していたため、初回コミットから
  ずっとカテゴリ無しで公開されていた。新規記事は upload.py 側で自動付与
  されるようになったので、過去記事をここで揃える。

やること（各記事）:
  1. AtomPub で記事を取得し、既に <category> があればスキップ（FORCE=1で上書き）
  2. 本文からシチュエーション情報を復元する
       - 舞台 : タイトル/本文の【…】
       - 見どころ: 「見どころは「A × B」」形式のカッコ内
  3. 画像を落として nsfw_detect で判定（ヌード相当か / 水着が見えているか）
  4. upload.decide_categories() に渡して、新規投稿とまったく同じ規則で決定
  5. <title> の直後に <category term="..."/> を差し込んで PUT
  6. 結果を CSV に書き出す（backfill_categories_report.csv）

安全設計:
  - 既定は DRY-RUN。実際に書き換えるのは APPLY=1 のときだけ。
  - 生XMLへの差し込みなので本文・タイトル・日時は温存する。
  - BACKFILL_LIMIT=N で先頭N件だけ、BACKFILL_SLEEP でリクエスト間隔。

環境変数: LIVEDOOR_USER_ID / LIVEDOOR_API_KEY / LIVEDOOR_BLOG_NAME
"""
import os
import re
import sys
import csv
import time
import html

import requests

from upload import (
    BLOG_NAME, LIVEDOOR_USER_ID, LIVEDOOR_API_KEY,
    get_headers, decide_categories,
)
from audit_titles import (
    iter_entries, fetch_entry, first_image_url, classify_url,
    edit_url_from_xml, TITLE_RE, PREFIX_RE,
)

APPLY = os.environ.get("APPLY", "").lower() in ("1", "true", "yes")
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")
LIMIT = int(os.environ.get("BACKFILL_LIMIT", "0") or "0")
SLEEP = float(os.environ.get("BACKFILL_SLEEP", "1.0") or "1.0")
REPORT = os.environ.get("BACKFILL_REPORT", "backfill_categories_report.csv")

CATEGORY_RE = re.compile(r"<category\b[^>]*/?>", re.IGNORECASE)
CATEGORY_TERM_RE = re.compile(r'<category\b[^>]*\bterm="([^"]*)"', re.IGNORECASE)
# 本文の「今日の舞台は【夜プール × 屋外】。」等
BRACKET_RE = re.compile(r"【([^】]+)】")
# 本文の「見どころは「マイクロビキニ × バキバキ腹筋」。」等
FEATURE_RE = re.compile(r"(?:見どころは|注目ポイントは|今日の推し要素は)「([^」]+)」")
TAG_RE = re.compile(r"<[^>]+>")

# タイトルテンプレート由来の【】（記事固有の舞台ではない）
NON_SCENE_BRACKETS = {"朗報", "悲報", "速報", "急募", "画像", "衝撃", "定期"}


def existing_categories(xml_text):
    return [html.unescape(t) for t in CATEGORY_TERM_RE.findall(xml_text)]


def extract_scene_from_article(title, xml_text):
    """公開済み記事の文面から (places, feats) を復元する。

    投稿時に本文へ書き込んだシチュエーション段落を読み戻すだけなので、
    素材ファイルが手元に無くても新規投稿と同じ判定材料が揃う。
    """
    text = html.unescape(TAG_RE.sub(" ", xml_text))

    places = []
    for chunk in BRACKET_RE.findall(title or "") + BRACKET_RE.findall(text):
        for p in chunk.split("×"):
            p = p.strip()
            if p and p not in NON_SCENE_BRACKETS and p not in places:
                places.append(p)

    feats = []
    for chunk in FEATURE_RE.findall(text):
        for f in chunk.split("×"):
            f = f.strip()
            if f and f not in feats:
                feats.append(f)

    return places[:2], feats[:3]


def insert_categories(xml_text, categories):
    """<title> の直後に <category> を差し込む（既存の <category> は除去）。"""
    xml_text = CATEGORY_RE.sub("", xml_text)
    block = "".join(
        '\n  <category term="{}" />'.format(html.escape(c, quote=True))
        for c in categories
    )
    return TITLE_RE.sub(lambda m: m.group(0) + block, xml_text, count=1)


def judge_article(image_url, title):
    """(nsfw, detection, note) を返す。画像判定が使えなければタイトル接頭辞に頼る。"""
    det = classify_url(image_url) if image_url else None
    if det and det.get("available"):
        return bool(det.get("nsfw")), det, " ".join(det.get("reasons") or []) or "no exposure"
    # 判定不能: 既存タイトルの "NSFW - " 接頭辞をそのまま尊重する（勝手に外さない）
    has_prefix = bool(PREFIX_RE.match(title or "")) and not (title or "").lower().lstrip().startswith("not")
    return has_prefix, {}, "undetectable:title-prefix"


def main():
    if not all([LIVEDOOR_USER_ID, LIVEDOOR_API_KEY, BLOG_NAME]):
        print("Error: missing LIVEDOOR_USER_ID / LIVEDOOR_API_KEY / LIVEDOOR_BLOG_NAME")
        return 1

    mode = "本番（PUTで更新）" if APPLY else "DRY-RUN（PUTしない）"
    print(f"=== 過去記事カテゴリ付与: {mode}{' / 既存カテゴリも上書き' if FORCE else ''} ===\n")

    # フィードのページ送りは同じ記事を複数回返すことがあるので、順序を保ったまま重複を除く
    ids, seen = [], set()
    for aid in iter_entries():
        if aid not in seen:
            seen.add(aid)
            ids.append(aid)
    print(f"対象記事: {len(ids)} 件（重複除去後）")
    if LIMIT > 0:
        ids = ids[:LIMIT]
        print(f"（BACKFILL_LIMIT により先頭 {len(ids)} 件のみ処理）")

    rows = []
    changed = skipped = failed = 0
    tally = {}

    for i, aid in enumerate(ids, 1):
        url, xml_text, err = fetch_entry(aid)
        if err:
            print(f"[{i}/{len(ids)}] {aid} 取得失敗: {err}")
            rows.append([aid, "", "", "", f"fail:{err}"])
            failed += 1
            time.sleep(SLEEP)
            continue

        m = TITLE_RE.search(xml_text)
        title = html.unescape(m.group(1)) if m else ""
        current = existing_categories(xml_text)

        if current and not FORCE:
            print(f"[{i}/{len(ids)}] {aid} カテゴリ済み（{' / '.join(current)}）→ スキップ")
            rows.append([aid, title, " / ".join(current), "", "skip:already"])
            skipped += 1
            time.sleep(SLEEP)
            continue

        places, feats = extract_scene_from_article(title, xml_text)
        img = first_image_url(xml_text)
        nsfw, det, note = judge_article(img, title)
        cats = decide_categories("", places, feats, nsfw=nsfw, detection=det)

        for c in cats:
            tally[c] = tally.get(c, 0) + 1

        print(f"[{i}/{len(ids)}] {aid} → {' / '.join(cats)}")
        print(f"    {title}")
        print(f"    舞台={places or '-'} 見どころ={feats or '-'} 判定={note}")

        action = "set"
        if APPLY:
            new_xml = insert_categories(xml_text, cats)
            r = requests.put(
                edit_url_from_xml(xml_text, url),
                data=new_xml.encode("utf-8"),
                headers=get_headers(LIVEDOOR_USER_ID, LIVEDOOR_API_KEY),
                timeout=60,
            )
            if r.status_code in (200, 201):
                print(f"    → OK ({r.status_code})")
                changed += 1
                action = "set:applied"
            else:
                print(f"    → 失敗 ({r.status_code}): {r.text[:200]}")
                failed += 1
                action = "set:failed"
        else:
            changed += 1
            action = "set:dry-run"

        rows.append([aid, title, " / ".join(current), " / ".join(cats), action])
        time.sleep(SLEEP)

    with open(REPORT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "before", "after", "action"])
        w.writerows(rows)

    print(f"\n=== 完了: 付与{changed} / スキップ{skipped} / 失敗{failed} ===")
    if tally:
        print("カテゴリ内訳:")
        for name, n in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"  {name}: {n}")
    print(f"レポート: {REPORT}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
