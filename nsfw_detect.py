# -*- coding: utf-8 -*-
"""画像そのものを見てNSFW判定＋コンテンツ種別を返すモジュール。

背景（2026-07-24 修正）:
  旧実装は「Driveのフォルダ名／ファイル名に NSFW 等の語が入っているか」で
  タイトルに "NSFW - " を付けていた。ところがライブラリのフォルダ自体が
  NSFW名だったため、ビキニ止まりの画像まで全部 "NSFW - " が付き、
  警告ラベルが意味を成さなくなっていた（RSS上でほぼ全記事がNSFW表記）。

方針:
  - 判定はファイル名やフォルダ名ではなく **画像の中身**（nudenet / ONNX）で行う。
  - 「露出＝ヌード」だけをNSFWとする。マイクロビキニ・尻の露出は
    このブランドの通常コンテンツなのでNSFWにしない（ラベルのインフレ防止）。
  - 判定不能（ライブラリ未導入・破損画像）のときは、ファイル名に露骨な
    行為ワードがある場合のみNSFW扱いにする保守フォールバック。

nudenet 3.x はモデル(320n.onnx 12MB)をwheelに同梱しているので、
GitHub Actions 上でも実行時ダウンロードは発生しない。
"""
import os
import re

# --- 検出しきい値（環境変数で調整可能） -----------------------------------
def _f(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


TH_GENITALIA = _f("NSFW_TH_GENITALIA", 0.45)   # 性器・肛門は低めのしきい値で拾う
TH_BREAST = _f("NSFW_TH_BREAST", 0.55)         # 胸の露出（単発）
TH_BREAST_PAIR = _f("NSFW_TH_BREAST_PAIR", 0.45)  # 両胸が同時に出ている＝トップレス

# 露出＝NSFW とみなすクラス
HARD_CLASSES = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_EXPOSED",
}
# 尻・脇・腹の露出は「水着コンテンツ」の通常域なのでNSFWにしない
SOFT_EXPOSED = {"BUTTOCKS_EXPOSED", "ARMPITS_EXPOSED", "BELLY_EXPOSED", "FEET_EXPOSED"}

# ファイル名にこれがあれば画像判定によらずNSFW（生成プロンプトが残っている場合）
EXPLICIT_NAME_RE = re.compile(
    r"(?i)(\bsex\b|fellatio|blowjob|handjob|paizuri|armpit[\s_\-]*fucking|"
    r"penis|peniss|pussy|vagina|cum\b|creampie|nipples?\b|nude|naked|topless|"
    r"全裸|ヌード|トップレス)"
)
# 「NSFW」「adult」「エロ」等の“フォルダ名レベル”の語は誤爆源なので使わない。

_detector = None
_detector_failed = False


def _get_detector():
    """NudeDetector を遅延ロード。使えなければ None（フォールバックへ）。"""
    global _detector, _detector_failed
    if _detector is not None or _detector_failed:
        return _detector
    try:
        from nudenet import NudeDetector
        _detector = NudeDetector()
    except Exception as e:  # 未導入 / ロード失敗
        print(f"[nsfw_detect] detector unavailable: {e}")
        _detector_failed = True
        return None
    return _detector


def _raw_detect(image_path):
    """nudenet の生検出結果。失敗時は None（判定不能）。"""
    det = _get_detector()
    if det is None:
        return None
    try:
        return det.detect(image_path)
    except Exception as e:
        # cv2 は非ASCIIパスを開けないので、ASCII名の一時ファイルに写して再試行
        try:
            import shutil, tempfile
            ext = os.path.splitext(image_path)[1] or ".png"
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
                tmp = tf.name
            shutil.copyfile(image_path, tmp)
            try:
                return det.detect(tmp)
            finally:
                os.unlink(tmp)
        except Exception as e2:
            print(f"[nsfw_detect] detect failed: {e} / {e2}")
            return None


def classify_image(image_path):
    """画像を判定して dict を返す。

    戻り値:
      available : 画像判定が成立したか（False ならファイル名フォールバック）
      nsfw      : ヌード相当か
      reasons   : NSFW判定の根拠ラベル
      swimwear  : 水着・ビキニ着用が見えるか（カテゴリ推定に使う）
      top       : 上位検出（ログ用）
    """
    name = os.path.basename(str(image_path or ""))
    name_hit = bool(EXPLICIT_NAME_RE.search(name))

    raw = _raw_detect(image_path)
    if raw is None:
        return {
            "available": False,
            "nsfw": name_hit,
            "reasons": ["filename:explicit"] if name_hit else [],
            "swimwear": False,
            "top": [],
        }

    best = {}
    counts = {}
    for d in raw:
        cls, score = d.get("class"), float(d.get("score", 0))
        if cls is None:
            continue
        best[cls] = max(best.get(cls, 0.0), score)
        if score >= TH_BREAST_PAIR:
            counts[cls] = counts.get(cls, 0) + 1

    reasons = []
    for cls in HARD_CLASSES:
        if best.get(cls, 0) >= TH_GENITALIA:
            reasons.append(f"{cls}:{best[cls]:.2f}")
    if best.get("FEMALE_BREAST_EXPOSED", 0) >= TH_BREAST:
        reasons.append(f"FEMALE_BREAST_EXPOSED:{best['FEMALE_BREAST_EXPOSED']:.2f}")
    elif counts.get("FEMALE_BREAST_EXPOSED", 0) >= 2:
        reasons.append("FEMALE_BREAST_EXPOSED:pair")  # 両胸＝トップレス
    if name_hit:
        reasons.append("filename:explicit")

    swimwear = (
        best.get("FEMALE_BREAST_COVERED", 0) >= 0.4
        or best.get("FEMALE_GENITALIA_COVERED", 0) >= 0.4
        or best.get("BUTTOCKS_EXPOSED", 0) >= 0.5
    )

    top = sorted(
        [(c, round(s, 2)) for c, s in best.items() if s >= 0.3],
        key=lambda x: -x[1],
    )[:6]

    return {
        "available": True,
        "nsfw": bool(reasons),
        "reasons": reasons,
        "swimwear": swimwear,
        "top": top,
    }


if __name__ == "__main__":  # 手元確認用: python nsfw_detect.py <画像...>
    import sys
    for p in sys.argv[1:]:
        r = classify_image(p)
        flag = "NSFW" if r["nsfw"] else "safe"
        print(f"{flag:5} {os.path.basename(p)[:60]:62} {r['reasons']} {r['top']}")
