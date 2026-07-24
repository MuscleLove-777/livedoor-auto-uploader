# -*- coding: utf-8 -*-
"""公開済み記事のNSFW接頭辞を、画像の中身を見て付け直す監査スクリプト。

背景:
  タイトルの "NSFW - " は素材フォルダ名から付けていたため、ライブラリの
  フォルダ名がNSFW系だっただけでビキニ止まりの記事にも全部付いてしまい、
  ほぼ全記事がNSFW表記＝警告として機能しない状態になっていた。

やること（各記事）:
  1. AtomPub で記事を取得し、本文の最初の <img> の画像URLを拾う
  2. 画像をダウンロードして nsfw_detect で判定（ヌード相当かどうか）
  3. 判定と現在のタイトルがズレていれば <title> だけ書き換えて PUT
       - ヌードなのに接頭辞なし → "NSFW - " を付ける
       - ヌードでないのに接頭辞あり → 接頭辞を外す
       - 紛らわしい "Not NSFW - " は常に除去する
  4. 結果を CSV に書き出す（audit_titles_report.csv）

安全設計:
  - 既定は DRY-RUN。実際に書き換えるのは APPLY=1 のときだけ。
  - 生XMLの最初の <title> だけ置換して PUT するので本文・カテゴリ・日時は温存。
  - AUDIT_LIMIT=N で先頭N件だけ、AUDIT_SLEEP でリクエスト間隔。

環境変数: LIVEDOOR_USER_ID / LIVEDOOR_API_KEY / LIVEDOOR_BLOG_NAME
"""
import os
import re
import sys
import csv
import time
import html
import tempfile
from xml.etree import ElementTree as ET

import requests

from upload import ATOM_BASE, BLOG_NAME, LIVEDOOR_USER_ID, LIVEDOOR_API_KEY, get_headers
from nsfw_detect import classify_image

APPLY = os.environ.get("APPLY", "").lower() in ("1", "true", "yes")
LIMIT = int(os.environ.get("AUDIT_LIMIT", "0") or "0")
SLEEP = float(os.environ.get("AUDIT_SLEEP", "1.0") or "1.0")
REPORT = os.environ.get("AUDIT_REPORT", "audit_titles_report.csv")

ATOM_NS = "http://www.w3.org/2005/Atom"
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
PREFIX = "NSFW - "
# 先頭の "NSFW - " / "Not NSFW - "（全角ダッシュや区切り無しも許容）
PREFIX_RE = re.compile(r"(?i)^\s*(not[\s_\-]*)?nsfw[\s:：\-ー–—]*")


MAX_PAGES = int(os.environ.get("AUDIT_MAX_PAGES", "50") or "50")


def _ids_in_feed(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"Feed parse error: {e}")
        return [], None
    ids = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        eid = entry.findtext(f"{{{ATOM_NS}}}id") or ""
        m = re.search(r"(\d{5,})", eid)
        if not m:
            for link in entry.findall(f"{{{ATOM_NS}}}link"):
                if link.get("rel") in ("edit", "service.edit"):
                    m = re.search(r"(\d{5,})", link.get("href", ""))
                    break
        if m:
            ids.append(m.group(1))
    nxt = None
    for link in root.findall(f"{{{ATOM_NS}}}link"):
        if link.get("rel") == "next" and link.get("href"):
            nxt = link.get("href")
            break
    return ids, nxt


def _ids_from_uploaded_log(path="uploaded.json"):
    """投稿ログに残っている記事URLからもIDを拾う（フィードが古い分を返さない対策）。"""
    ids = []
    try:
        import json
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for e in data.get("files", []):
            if isinstance(e, dict) and e.get("article_url"):
                m = re.search(r"(\d{5,})", e["article_url"])
                if m:
                    ids.append(m.group(1))
    except Exception as e:
        print(f"uploaded.json skipped: {e}")
    return ids


def iter_entries():
    """記事IDを全件返す。フィードのページ送り＋投稿ログの両方から集めて重複除去。

    ライブドアのフィードは rel=next を返さないことがあるので、
    その場合は ?page=N を進めて新しいIDが出なくなるまで辿る。
    """
    base = ATOM_BASE.format(blog_name=BLOG_NAME) + "/article"
    headers = get_headers(LIVEDOOR_USER_ID, LIVEDOOR_API_KEY)
    seen = set()
    out = []

    url, page = base, 1
    while url and page <= MAX_PAGES:
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code != 200:
            print(f"Feed GET failed: {r.status_code} {r.text[:200]}")
            break
        ids, nxt = _ids_in_feed(r.text)
        fresh = [i for i in ids if i not in seen]
        for i in fresh:
            seen.add(i)
            out.append(i)
        print(f"  feed page {page}: {len(ids)} entries ({len(fresh)} new)")
        if not fresh:
            break
        page += 1
        url = nxt or f"{base}?page={page}"
        time.sleep(SLEEP)

    for i in _ids_from_uploaded_log():
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
        return url, None, f"GET {r.status_code}"
    return url, r.text, None


def first_image_url(xml_text):
    for src in IMG_RE.findall(xml_text):
        src = html.unescape(src)
        if "blogimg.jp" in src or src.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp")
        ):
            return src
    return None


def classify_url(image_url):
    """画像URLを一時ファイルに落として判定。失敗時 None。"""
    try:
        r = requests.get(image_url, timeout=60)
        if r.status_code != 200 or not r.content:
            return None
        ext = os.path.splitext(image_url.split("?")[0])[1] or ".png"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
            tf.write(r.content)
            tmp = tf.name
        try:
            return classify_image(tmp)
        finally:
            os.unlink(tmp)
    except Exception as e:
        print(f"  image fetch/classify failed: {e}")
        return None


def edit_url_from_xml(xml_text, fallback):
    try:
        root = ET.fromstring(xml_text)
        for link in root.findall(f".//{{{ATOM_NS}}}link"):
            if link.get("rel") in ("edit", "service.edit") and link.get("href"):
                return link.get("href")
    except ET.ParseError:
        pass
    return fallback


def desired_title(current, nsfw):
    """判定に合わせた正しいタイトルを返す（変更不要なら current と同じ）。"""
    base = PREFIX_RE.sub("", current).strip()
    if not base:               # 接頭辞しか無い異常タイトルは触らない
        return current
    return (PREFIX + base) if nsfw else base


def main():
    if not all([LIVEDOOR_USER_ID, LIVEDOOR_API_KEY, BLOG_NAME]):
        print("Error: missing LIVEDOOR_USER_ID / LIVEDOOR_API_KEY / LIVEDOOR_BLOG_NAME")
        return 1

    mode = "本番（PUTで更新）" if APPLY else "DRY-RUN（PUTしない）"
    print(f"=== タイトルNSFW監査: {mode} ===\n")

    ids = list(iter_entries())
    print(f"対象記事: {len(ids)} 件")
    if LIMIT > 0:
        ids = ids[:LIMIT]
        print(f"（AUDIT_LIMIT により先頭 {len(ids)} 件のみ処理）")

    rows = []
    changed = unchanged = skipped = failed = 0

    for i, aid in enumerate(ids, 1):
        url, xml_text, err = fetch_entry(aid)
        if err:
            print(f"[{i}/{len(ids)}] {aid} 取得失敗: {err}")
            failed += 1
            time.sleep(SLEEP)
            continue

        m = TITLE_RE.search(xml_text)
        title = html.unescape(m.group(1)) if m else None
        img = first_image_url(xml_text)
        if not title or not img:
            print(f"[{i}/{len(ids)}] {aid} タイトル/画像が取れずスキップ")
            rows.append([aid, title or "", img or "", "", "", "skip:no-image"])
            skipped += 1
            time.sleep(SLEEP)
            continue

        det = classify_url(img)
        if det is None or not det.get("available"):
            print(f"[{i}/{len(ids)}] {aid} 画像判定不能 → 触らない")
            rows.append([aid, title, img, "", "", "skip:undetectable"])
            skipped += 1
            time.sleep(SLEEP)
            continue

        nsfw = bool(det["nsfw"])
        new_title = desired_title(title, nsfw)
        reasons = " ".join(det.get("reasons") or [])

        if new_title == title:
            rows.append([aid, title, img, "NSFW" if nsfw else "safe", reasons, "ok"])
            unchanged += 1
            time.sleep(SLEEP)
            continue

        action = "add-prefix" if nsfw else "remove-prefix"
        print(f"[{i}/{len(ids)}] {aid} {action}")
        print(f"    旧: {title}")
        print(f"    新: {new_title}   ({reasons or 'no exposure'})")

        if APPLY:
            put_target = edit_url_from_xml(xml_text, url)
            new_xml = TITLE_RE.sub(
                f"<title>{html.escape(new_title, quote=False)}</title>", xml_text, count=1
            )
            r = requests.put(
                put_target,
                data=new_xml.encode("utf-8"),
                headers=get_headers(LIVEDOOR_USER_ID, LIVEDOOR_API_KEY),
                timeout=60,
            )
            if r.status_code in (200, 201):
                print(f"    → OK ({r.status_code})")
                changed += 1
                action += ":applied"
            else:
                print(f"    → 失敗 ({r.status_code}): {r.text[:200]}")
                failed += 1
                action += ":failed"
        else:
            changed += 1
            action += ":dry-run"

        rows.append([aid, title, img, "NSFW" if nsfw else "safe", reasons, action])
        time.sleep(SLEEP)

    with open(REPORT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "image_url", "judge", "reasons", "action"])
        w.writerows(rows)

    print(f"\n=== 完了: 要修正{changed} / 変更不要{unchanged} / スキップ{skipped} / 失敗{failed} ===")
    print(f"レポート: {REPORT}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
