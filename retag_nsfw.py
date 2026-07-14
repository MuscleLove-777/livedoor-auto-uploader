# -*- coding: utf-8 -*-
"""
既存ライブドアブログ記事に「NSFW - 」接頭辞を後付けする一回限りのユーティリティ。

背景: upload.py はタイトルを「{カテゴリ} - {テンプレ}」で生成し、カテゴリが
Drive フォルダ名 "NSFW" のときだけ NSFW 接頭辞が付く。過去のヌード記事の多くは
別名フォルダ由来・または50字超で接頭辞脱落しており、無警告で公開されている。
本スクリプトは retag_targets.txt に列挙した記事IDのタイトル先頭に "NSFW - " を
冪等に付与する（既に NSFW で始まる記事はスキップ）。

安全設計:
  - AtomPub で記事を GET し、生XMLの最初の <title>...</title> だけを置換して PUT。
    本文(CDATA)・カテゴリ・公開日時はそのまま送り返すので消えない。
  - DRY_RUN=1 なら GET と変更予定の表示のみで PUT しない。
  - RETAG_LIMIT=N で先頭N件だけ処理（本番前の1件検証用）。
  - RETAG_SLEEP 秒で各リクエスト間ウェイト（既定1.5s）。

環境変数: LIVEDOOR_USER_ID / LIVEDOOR_API_KEY / LIVEDOOR_BLOG_NAME
  （既存 upload.py と同じ GitHub Actions secrets を再利用）
"""
import os
import re
import sys
import time
import html
from xml.etree import ElementTree as ET

import requests

from upload import (
    ATOM_BASE,
    BLOG_NAME,
    LIVEDOOR_USER_ID,
    LIVEDOOR_API_KEY,
    get_headers,
)

TARGETS_FILE = os.environ.get("RETAG_TARGETS", "retag_targets.txt")
PREFIX = "NSFW - "
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
LIMIT = int(os.environ.get("RETAG_LIMIT", "0") or "0")
SLEEP = float(os.environ.get("RETAG_SLEEP", "1.5") or "1.5")

ATOM_NS = "http://www.w3.org/2005/Atom"
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def load_targets():
    """retag_targets.txt から記事IDを読む（# 始まりと空行は無視）。"""
    if not os.path.exists(TARGETS_FILE):
        print(f"Targets file not found: {TARGETS_FILE}")
        return []
    ids = []
    with open(TARGETS_FILE, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            # "12345" でも URL でも先頭の数値列を拾う
            m = re.search(r"(\d{5,})", s)
            if m:
                ids.append(m.group(1))
    # 重複除去（順序維持）
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def member_url(article_id):
    return ATOM_BASE.format(blog_name=BLOG_NAME) + f"/article/{article_id}"


def fetch_entry(article_id):
    url = member_url(article_id)
    r = requests.get(url, headers=get_headers(LIVEDOOR_USER_ID, LIVEDOOR_API_KEY), timeout=60)
    if r.status_code != 200:
        return None, None, f"GET {r.status_code}: {r.text[:200]}"
    return url, r.text, None


def current_title(xml_text):
    m = TITLE_RE.search(xml_text)
    if not m:
        return None
    return html.unescape(m.group(1))


def edit_url_from_xml(xml_text, fallback):
    """rel=edit / service.edit のリンクがあれば PUT 先に使う。無ければ fallback。"""
    try:
        root = ET.fromstring(xml_text)
        for link in root.findall(f".//{{{ATOM_NS}}}link"):
            if link.get("rel") in ("edit", "service.edit"):
                href = link.get("href")
                if href:
                    return href
    except ET.ParseError:
        pass
    return fallback


def build_updated_xml(xml_text, new_title):
    """生XMLの最初の <title> の中身だけ差し替える。他要素は温存。"""
    escaped = html.escape(new_title, quote=False)
    return TITLE_RE.sub(f"<title>{escaped}</title>", xml_text, count=1)


def put_entry(url, xml_text):
    headers = get_headers(LIVEDOOR_USER_ID, LIVEDOOR_API_KEY)
    r = requests.put(url, data=xml_text.encode("utf-8"), headers=headers, timeout=60)
    return r.status_code, r.text[:300]


def main():
    if not all([LIVEDOOR_USER_ID, LIVEDOOR_API_KEY, BLOG_NAME]):
        print("Error: missing LIVEDOOR_USER_ID / LIVEDOOR_API_KEY / LIVEDOOR_BLOG_NAME")
        return 1

    ids = load_targets()
    if not ids:
        print("No target ids.")
        return 0
    if LIMIT > 0:
        ids = ids[:LIMIT]

    mode = "DRY-RUN（PUTしない）" if DRY_RUN else "本番（PUTで更新）"
    print(f"=== NSFW retag: {mode} / 対象 {len(ids)} 件 ===\n")

    done = skipped = failed = 0
    for i, aid in enumerate(ids, 1):
        url, xml_text, err = fetch_entry(aid)
        if err:
            print(f"[{i}/{len(ids)}] {aid}  取得失敗: {err}")
            failed += 1
            time.sleep(SLEEP)
            continue

        title = current_title(xml_text)
        if title is None:
            print(f"[{i}/{len(ids)}] {aid}  <title>が見つからずスキップ")
            failed += 1
            time.sleep(SLEEP)
            continue

        stripped = title.lstrip()
        if stripped.upper().startswith("NSFW"):
            print(f"[{i}/{len(ids)}] {aid}  既にNSFW → スキップ: {title}")
            skipped += 1
            time.sleep(SLEEP)
            continue

        # 「Not NSFW - 」で始まる（ヌードなのに安全と偽っている）場合は
        # その接頭辞を丸ごと「NSFW - 」に置換する（二重表記を防ぐ）。
        m = re.match(r"(?i)^not[\s_\-]*nsfw[\s:：\-ー]*", stripped)
        if m:
            new_title = PREFIX + stripped[m.end():]
        else:
            new_title = PREFIX + title
        print(f"[{i}/{len(ids)}] {aid}")
        print(f"    旧: {title}")
        print(f"    新: {new_title}")

        if DRY_RUN:
            done += 1
            time.sleep(SLEEP)
            continue

        put_target = edit_url_from_xml(xml_text, url)
        new_xml = build_updated_xml(xml_text, new_title)
        code, resp = put_entry(put_target, new_xml)
        if code in (200, 201):
            print(f"    → OK ({code})")
            done += 1
        else:
            print(f"    → 失敗 ({code}): {resp}")
            failed += 1
        time.sleep(SLEEP)

    print(f"\n=== 完了: 更新{done} / 既NSFWスキップ{skipped} / 失敗{failed} ===")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
