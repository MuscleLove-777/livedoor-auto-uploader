# -*- coding: utf-8 -*-
"""
ライブドアブログ自動投稿（GitHub Actions用）
Google Driveからダウンロード → ランダム1ファイルを選択 → 画像付きブログ記事を投稿
AtomPub API（旧版）を使用
"""
import sys, json, os, random, time, hashlib, base64, datetime, re
from xml.etree import ElementTree as ET

import requests
import gdown

# ============================================================
# 設定
# ============================================================

GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")
LIVEDOOR_USER_ID = os.environ.get("LIVEDOOR_USER_ID", "")
LIVEDOOR_API_KEY = os.environ.get("LIVEDOOR_API_KEY", "")
BLOG_NAME = os.environ.get("LIVEDOOR_BLOG_NAME", "")

PATREON_LINK = "https://www.patreon.com/cw/MuscleLove"
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # ライブドアブログ画像上限: 10MB
UPLOADED_LOG = "uploaded.json"

# AtomPub API（旧版）ベースURL
ATOM_BASE = "https://livedoor.blogcms.jp/atom/blog/{blog_name}"

# ============================================================
# 記事タイトルテンプレート（ライブドアブログ人気ブログ風）
# 【】付き、数字入り、好奇心を引くスタイル
# ============================================================
TITLE_TEMPLATES = [
    # 好奇心系
    "【衝撃】この筋肉美、見たことある？",
    "【保存版】筋肉女子の美しさがヤバすぎる件",
    "【圧巻】鍛え抜かれた身体がここに",
    "【必見】こんな筋肉美、他にない",
    "【驚愕】女性の筋肉美ここに極まれり",
    # ストーリー系
    "今日出会った最高の筋肉美を紹介する",
    "この鍛え上げた身体、反則でしょ...",
    "筋肉女子の魅力が止まらない件について",
    "見てくれ、この圧倒的な肉体美を",
    "これが本物のフィットネスボディ",
    # カジュアル・会話調
    "もうね、この筋肉に惚れた（直球）",
    "筋肉女子の破壊力がエグい",
    "バキバキボディの美しさを語りたい",
    "今日の筋肉女子が最高すぎたｗ",
    "鍛え抜いた身体って、なんでこんなに美しいの",
    # 英語ミックス
    "【Muscle Queen】今日のベストショット",
    "【Strong is Beautiful】鍛えた女性は美しい",
    "【Iron Goddess】圧倒的筋肉美",
    "【Power & Beauty】強さと美しさの共存",
    "【Gym Goddess】フィットネスの女神",
    # 問いかけ系
    "筋肉女子の魅力、あなたは気づいてる？",
    "なぜ鍛えた女性はこんなに美しいのか",
    "筋トレ女子を推さない理由がない",
]

# ============================================================
# ブログ記事テンプレート（人気ライブドアブログの書き方 × MuscleLove文体）
# 書き出し → 画像 → 本文 → まとめ → CTA の構成
# ============================================================
BLOG_BODY_TEMPLATES = [
    # テンプレ1: カジュアル・興奮系
    {
        'opening': [
            'どうも、MuscleLoveです💪',
            'やっほー、MuscleLoveやで💪',
            'MuscleLoveです！今日もいくぞ🔥',
        ],
        'intro': [
            'いやー、今日もヤバいの見つけてしまった。',
            '今日の一枚、マジでやばい。語彙力失うレベル。',
            'はい来ました。これは保存確定ですわ。',
            'もうね、こういうの見ると元気出るよね。',
        ],
        'body': [
            'この引き締まった身体、見てくれよ。<br>'
            '鍛え上げた筋肉の一つ一つが美しい。<br>'
            'こういう肉体美って、日々の努力の結晶なんだよな。',

            'バキバキに仕上がった身体。<br>'
            '筋肉のカット、ポージング、全部が芸術。<br>'
            'これぞ鍛え抜いた者だけが持てる美しさ。',

            '迫力ある筋肉美と色気あふれるポーズ。<br>'
            '汗ばむ肌と浮き出る筋肉のコントラストがたまらん。<br>'
            '強さと美しさって共存するんだよな。',
        ],
        'closing': [
            'やっぱ筋肉女子は最高だわ（確信）',
            'これだから筋肉女子の推し活はやめられない',
            '今日もいい筋肉を見て、いい1日だった',
        ],
    },
    # テンプレ2: 解説・豆知識系
    {
        'opening': [
            'MuscleLoveです！',
            'こんにちは、MuscleLoveです✨',
        ],
        'intro': [
            '今日は筋肉美の魅力について語りつつ、最高の一枚を紹介します。',
            '筋トレ女子の美しさ、伝わってますか？今日も全力で紹介します。',
            '鍛えた女性の身体って本当に美しい。今日もその魅力をお届け。',
        ],
        'body': [
            '■ 筋肉女子が美しい理由<br><br>'
            '鍛え上げた筋肉には、日々のストイックな努力が詰まってる。<br>'
            '食事管理、トレーニング、休息のバランス。<br>'
            'その全てが身体に表れるから、こんなに美しいんだよな。<br><br>'
            '今日の一枚も、まさにその結晶。',

            '■ なぜ筋肉美に惹かれるのか<br><br>'
            '筋肉って「努力の可視化」なんだよね。<br>'
            '毎日のトレーニングが、そのまま身体のラインに出る。<br>'
            '嘘がつけない。だから美しい。<br><br>'
            '今日のショットも、その美しさが詰まってる。',
        ],
        'closing': [
            '筋肉女子の魅力、少しでも伝わったら嬉しい！',
            '鍛えた身体の美しさ、これからも発信していくよ💪',
            'もっと筋肉女子の世界を知りたい人は、ぜひ見てって！',
        ],
    },
    # テンプレ3: ストーリー・シチュエーション系
    {
        'opening': [
            'MuscleLoveです🔥',
            'どうも！MuscleLoveです💪',
        ],
        'intro': [
            'ある日のジムにて。こんな光景に出会ったら、目が離せなくなるよな。',
            '今日は特別な一枚。この筋肉美、ストーリーを感じない？',
            '想像してみてくれ。目の前にこの肉体美があったら。',
        ],
        'body': [
            '鍛え抜かれた身体から放たれるオーラ。<br>'
            '一つ一つの筋肉が語りかけてくるような迫力。<br>'
            'これだけのフィジークを作り上げるには、<br>'
            '想像を超える努力があったはず。<br><br>'
            'それでも彼女たちは笑顔で、軽々とポーズを決める。<br>'
            'かっこよすぎないか...？',

            'この身体を見てほしい。<br>'
            'シュレッドされた腹筋、盛り上がった肩、引き締まった脚。<br>'
            '全てが完璧なバランスで仕上がってる。<br><br>'
            'こういう肉体美を見ると、<br>'
            '「人間の身体ってここまでいけるんだ」って感動するよな。',
        ],
        'closing': [
            'こういう出会いがあるから、筋肉女子の世界はやめられない',
            '最高の筋肉美をお届けできたかな？',
            '今日もいい筋肉に出会えた。感謝。',
        ],
    },
    # テンプレ4: 超短文・テンポ系（モバイル向き）
    {
        'opening': [
            'MuscleLove💪',
            '🔥MuscleLove🔥',
        ],
        'intro': [
            'はい、今日のベストショット。',
            '見てくれ。',
            '今日の一枚。',
        ],
        'body': [
            'この筋肉。<br>'
            'この迫力。<br>'
            'この美しさ。<br><br>'
            '語彙力？いらん。見ればわかる。',

            'バキバキ。<br>'
            'シュレッド。<br>'
            'パーフェクト。<br><br>'
            '以上。（褒め言葉）',

            '強い。<br>'
            '美しい。<br>'
            '最高。<br><br>'
            '筋肉女子、推すしかない。',
        ],
        'closing': [
            'はい優勝🏆',
            '今日も筋肉に感謝✨',
            '以上！また明日💪',
        ],
    },
    # テンプレ5: 問いかけ・読者参加系
    {
        'opening': [
            'MuscleLoveです！今日はみんなに聞きたいことがある💪',
            'こんにちは、MuscleLoveです！',
        ],
        'intro': [
            '突然だけど、筋肉女子の魅力って何だと思う？',
            'あなたが筋肉女子に惹かれるポイント、どこ？',
            '今日はこの写真を見て、率直な感想を聞かせてほしい。',
        ],
        'body': [
            '俺はね、やっぱりこの「鍛え抜いた感」がたまらないんよ。<br><br>'
            '腹筋のカット、肩のキャップ、背中の広がり。<br>'
            '全部が努力の証。<br>'
            'その覚悟と結果が身体に刻まれてるのが、最高にかっこいい。<br><br>'
            'あなたはどう思う？',

            '筋肉美の魅力って人それぞれだと思うんだけど、<br>'
            '俺が好きなのはこの「ストイックさが身体に出てる」ところ。<br><br>'
            '甘えなし、言い訳なし。<br>'
            'ただひたすら鍛え上げた結果がこれ。<br>'
            '美しくない？',
        ],
        'closing': [
            'コメントで教えてくれ！筋肉女子のどこが好き？💪',
            'あなたの推しポイント、コメントで語ろう🔥',
            'みんなの意見聞かせて！',
        ],
    },
]

# ハッシュタグ（ブログ本文に挿入）
BASE_HASHTAGS = [
    '筋トレ', '筋肉女子', 'フィットネス', 'ワークアウト', 'ジム',
    'musclegirl', 'fitness', 'strongwomen', 'workout', 'gym',
    'MuscleLove', 'FBB', 'fitnessmotivation', '筋トレ女子',
    '筋肉美', 'マッスルガール', 'フィジーク',
]

# コンテンツ推測用マッピング
CONTENT_TAG_MAP = {
    'training': ['筋トレ', 'トレーニング', 'workout'],
    'workout': ['筋トレ', 'ワークアウト', 'gym'],
    'pullups': ['懸垂', '背中トレ', 'pullups'],
    'posing': ['ポージング', 'ボディビル', 'posing'],
    'flex': ['フレックス', '筋肉', 'flex'],
    'muscle': ['筋肉', 'マッスル', 'muscle'],
    'bicep': ['上腕二頭筋', '腕トレ', 'biceps'],
    'abs': ['腹筋', 'シックスパック', 'abs'],
    'leg': ['脚トレ', 'レッグデイ', 'legs'],
    'back': ['背中', 'ラット', 'back'],
    'squat': ['スクワット', '脚トレ', 'squat'],
}


# ============================================================
# WSSE認証
# ============================================================

def create_wsse(user_id, api_key):
    """WSSE認証ヘッダーを生成"""
    created = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    b_nonce = hashlib.sha1(str(random.random()).encode()).digest()
    b_digest = hashlib.sha1(b_nonce + created.encode() + api_key.encode()).digest()
    wsse = (
        f'UsernameToken Username="{user_id}", '
        f'PasswordDigest="{base64.b64encode(b_digest).decode()}", '
        f'Nonce="{base64.b64encode(b_nonce).decode()}", '
        f'Created="{created}"'
    )
    return wsse


def get_headers(user_id, api_key, content_type='application/atom+xml'):
    """API呼び出し用のヘッダーを生成"""
    return {
        'X-WSSE': create_wsse(user_id, api_key),
        'Authorization': 'WSSE profile="UsernameToken"',
        'Content-Type': content_type,
    }


# ============================================================
# アップロード済み管理
# ============================================================

def load_uploaded_log():
    """アップロード済みファイルの記録を読み込む"""
    if not os.path.exists(UPLOADED_LOG):
        return {"files": []}
    with open(UPLOADED_LOG, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"files": data}
    return data


def save_uploaded_log(log_data):
    """アップロード済みファイルの記録を保存する"""
    with open(UPLOADED_LOG, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)


# ============================================================
# Google Driveダウンロード
# ============================================================

def download_media():
    """Google Driveフォルダから画像ファイルをダウンロードする"""
    dl_dir = "media"
    os.makedirs(dl_dir, exist_ok=True)
    url = f"https://drive.google.com/drive/folders/{GDRIVE_FOLDER_ID}"
    print(f"Downloading from Google Drive: {url}")
    try:
        gdown.download_folder(url, output=dl_dir, quiet=False, remaining_ok=True)
    except Exception as e:
        print(f"Download error: {e}")

    files = []
    for root, dirs, filenames in os.walk(dl_dir):
        for fname in filenames:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                size = os.path.getsize(fpath)
                if size <= MAX_FILE_SIZE:
                    files.append(fpath)
                else:
                    print(f"Skip (>10MB): {fname} ({size / 1024 / 1024:.1f}MB)")
    return files


# ============================================================
# タグ生成
# ============================================================

def generate_tags(file_path):
    """フォルダ名・ファイル名からタグを生成"""
    tags = list(BASE_HASHTAGS)

    path_lower = file_path.lower().replace('\\', '/').replace('-', ' ').replace('_', ' ')
    matched = set()
    for keyword, keyword_tags in CONTENT_TAG_MAP.items():
        if keyword in path_lower:
            for t in keyword_tags:
                if t not in matched:
                    tags.append(t)
                    matched.add(t)

    # 重複除去
    seen = set()
    unique = []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique.append(t)
    return unique


def sanitize_category(name, max_len=30):
    """フォルダ名からカテゴリ名を安全に抽出"""
    name = re.sub(r'[{}\[\]]', '', name)
    if ',' in name:
        name = name.split(',')[0].strip()
    name = name.strip(' -_')
    if len(name) > max_len:
        name = name[:max_len].rstrip(' -_')
    return name if name else "Muscle"


# ============================================================
# ライブドアブログ画像アップロード
# ============================================================

def upload_image(image_path):
    """画像をライブドアブログにアップロードし、画像URLを返す"""
    endpoint = ATOM_BASE.format(blog_name=BLOG_NAME) + '/image'

    ext = os.path.splitext(image_path)[1].lower()
    content_types = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.png': 'image/png', '.gif': 'image/gif',
        '.bmp': 'image/bmp', '.webp': 'image/webp',
    }
    ct = content_types.get(ext, 'image/jpeg')

    with open(image_path, 'rb') as f:
        binary_data = f.read()

    size_mb = len(binary_data) / 1024 / 1024
    print(f"Uploading image: {os.path.basename(image_path)} ({size_mb:.1f}MB)")

    headers = get_headers(LIVEDOOR_USER_ID, LIVEDOOR_API_KEY, content_type=ct)

    r = requests.post(endpoint, data=binary_data, headers=headers, timeout=120)

    if r.status_code not in (200, 201):
        print(f"Image upload failed: {r.status_code}")
        print(f"  Response: {r.text[:500]}")
        return None

    # レスポンスXMLから画像URLを抽出
    try:
        root = ET.fromstring(r.text)
        # Atomネームスペース
        ns = {'atom': 'http://www.w3.org/2005/Atom'}

        # <link rel="alternate" href="..."> から画像URLを取得
        for link in root.findall('.//atom:link', ns):
            if link.get('rel') == 'alternate':
                img_url = link.get('href', '')
                if img_url:
                    print(f"Image URL: {img_url}")
                    return img_url

        # <content src="..."> からも試す
        content = root.find('.//atom:content', ns)
        if content is not None:
            img_url = content.get('src', '')
            if img_url:
                print(f"Image URL (from content): {img_url}")
                return img_url

        # 最終手段：srcを含むテキストをパースする
        text = r.text
        src_match = re.search(r'src=["\']?(https?://[^"\'>\s]+)', text)
        if src_match:
            img_url = src_match.group(1)
            print(f"Image URL (regex): {img_url}")
            return img_url

        print(f"Could not extract image URL from response:")
        print(r.text[:500])
        return None

    except ET.ParseError as e:
        print(f"XML parse error: {e}")
        print(f"Response: {r.text[:500]}")
        return None


# ============================================================
# ブログ記事投稿
# ============================================================

def build_article_xml(title, body_html, category=None, draft=False):
    """AtomPub形式の記事XMLを構築"""
    draft_val = 'yes' if draft else 'no'
    category_xml = ''
    if category:
        category_xml = f'  <category term="{category}" />'

    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app"
       xmlns:blogcms="http://blogcms.jp/-/spec/atompub/1.0/">
  <title>{title}</title>
{category_xml}
  <blogcms:source>
    <blogcms:body><![CDATA[{body_html}]]></blogcms:body>
  </blogcms:source>
  <app:draft xmlns:app="http://www.w3.org/2007/app">{draft_val}</app:draft>
</entry>'''
    return xml


def build_blog_html(image_url, tags, file_path):
    """人気ライブドアブログ風の記事HTML本文を生成"""
    parts = file_path.replace('\\', '/').split('/')
    category = "Muscle"
    for p in parts:
        if p not in ['media', ''] and '.' not in p:
            category = sanitize_category(p)
            break

    # テンプレートをランダム選択
    template = random.choice(BLOG_BODY_TEMPLATES)

    opening = random.choice(template['opening'])
    intro = random.choice(template['intro'])
    body = random.choice(template['body'])
    closing = random.choice(template['closing'])

    hashtag_html = ' '.join([f'#{t}' for t in tags[:15]])

    html = f'''<p>{opening}</p>

<p>{intro}</p>

<p>&nbsp;</p>

<div style="text-align: center;">
<p><img src="{image_url}" alt="{category}" style="max-width: 100%;" /></p>
</div>

<p>&nbsp;</p>

<p>{body}</p>

<p>&nbsp;</p>

<p>{closing}</p>

<hr />

<div style="text-align: center; background: #1a1a2e; padding: 20px; border-radius: 10px; margin: 20px 0;">
<p style="font-size: 1.3em; color: #FFD700;">🔥 もっと見たい？ Patreonで限定コンテンツ公開中！</p>
<p style="font-size: 1.1em;"><a href="{PATREON_LINK}" target="_blank" rel="noopener" style="color: #00C9FF; text-decoration: underline;">
👉 MuscleLove on Patreon 👈
</a></p>
<p style="font-size: 0.9em; color: #ccc;">ここでしか見れない筋肉美をお届け中💪</p>
</div>

<p>&nbsp;</p>

<p style="color: #888; font-size: 0.85em;">{hashtag_html}</p>'''

    return html, category


def post_article(title, body_html, category=None):
    """記事をライブドアブログに投稿"""
    endpoint = ATOM_BASE.format(blog_name=BLOG_NAME) + '/article'

    xml = build_article_xml(title, body_html, category=category, draft=False)

    headers = get_headers(LIVEDOOR_USER_ID, LIVEDOOR_API_KEY)

    print(f"\nPosting article: {title}")
    r = requests.post(endpoint, data=xml.encode('utf-8'), headers=headers, timeout=60)

    if r.status_code not in (200, 201):
        print(f"Post failed: {r.status_code}")
        print(f"  Response: {r.text[:500]}")
        return None

    # レスポンスから記事URLを抽出
    try:
        root = ET.fromstring(r.text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}

        for link in root.findall('.//atom:link', ns):
            if link.get('rel') == 'alternate':
                article_url = link.get('href', '')
                if article_url:
                    print(f"Article published: {article_url}")
                    return article_url

        # IDから推測
        entry_id = root.find('.//atom:id', ns)
        if entry_id is not None:
            print(f"Article posted (ID: {entry_id.text})")
            return entry_id.text

    except ET.ParseError:
        pass

    print("Article posted (could not extract URL)")
    return "posted"


# ============================================================
# 認証テスト
# ============================================================

def test_auth():
    """認証が通るかテスト（カテゴリ一覧取得）"""
    endpoint = ATOM_BASE.format(blog_name=BLOG_NAME) + '/category'
    headers = get_headers(LIVEDOOR_USER_ID, LIVEDOOR_API_KEY)

    r = requests.get(endpoint, headers=headers, timeout=30)
    if r.status_code == 200:
        print(f"Auth OK (blog: {BLOG_NAME})")
        return True
    else:
        print(f"Auth failed: {r.status_code}")
        print(f"  Response: {r.text[:300]}")
        return False


# ============================================================
# メイン
# ============================================================

def main():
    print("=== Livedoor Blog Auto Poster (GitHub Actions) ===\n")

    if not all([LIVEDOOR_USER_ID, LIVEDOOR_API_KEY, BLOG_NAME, GDRIVE_FOLDER_ID]):
        print("Error: Missing required environment variables")
        print("Required: LIVEDOOR_USER_ID, LIVEDOOR_API_KEY, LIVEDOOR_BLOG_NAME, GDRIVE_FOLDER_ID")
        return 1

    # 認証テスト
    if not test_auth():
        print("Authentication failed. Check LIVEDOOR_USER_ID, LIVEDOOR_API_KEY, LIVEDOOR_BLOG_NAME")
        return 1

    # Load log
    log_data = load_uploaded_log()

    # Download media from Google Drive
    media_files = download_media()
    if not media_files:
        print("No image files found!")
        return 0

    # Filter out already uploaded
    if os.environ.get("UPLOAD_ALL", "").lower() in ("1", "true", "yes"):
        available = media_files
        print(f"\nUPLOAD_ALL enabled: all {len(available)} files are candidates")
    else:
        uploaded_names = [entry['file'] if isinstance(entry, dict) else entry
                          for entry in log_data.get("files", [])]
        available = [f for f in media_files if os.path.basename(f) not in uploaded_names]
        if not available:
            print("All files already uploaded!")
            return 0
        print(f"\nAvailable: {len(available)} / Total: {len(media_files)}")

    # Select random file
    selected = random.choice(available)
    fname = os.path.basename(selected)
    print(f"Selected: {fname}")

    # Generate tags
    tags = generate_tags(selected)

    # トレンドタグ追加
    try:
        from trending import get_trending_tags
        trend_tags = get_trending_tags(max_tags=5)
        if trend_tags:
            seen = {t.lower() for t in tags}
            for t in trend_tags:
                if t.lower() not in seen:
                    tags.append(t)
                    seen.add(t.lower())
    except Exception as e:
        print(f"Trend tags skipped: {e}")

    # Step 1: 画像アップロード
    image_url = upload_image(selected)
    if not image_url:
        print("Image upload failed!")
        return 1

    # Step 2: 記事HTML生成
    body_html, category = build_blog_html(image_url, tags, selected)

    # タイトル生成
    template = random.choice(TITLE_TEMPLATES)
    title = f"{category} - {template}" if category != "Muscle" else template
    if len(title) > 50:
        title = template

    print(f"Title: {title}")
    print(f"Tags: {', '.join(tags[:10])}...")
    print(f"Category: {category}")

    # Step 3: 記事投稿
    article_url = post_article(title, body_html, category=None)

    if not article_url:
        print("Article post failed!")
        return 1

    # Record uploaded file
    log_data["files"].append({
        'file': fname,
        'image_url': image_url,
        'article_url': article_url,
        'uploaded_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    })
    save_uploaded_log(log_data)

    remaining = len(available) - 1
    print(f"\nDone! Remaining: {remaining}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
