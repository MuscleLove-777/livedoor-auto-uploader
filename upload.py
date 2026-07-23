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

PATREON_LINK = "https://www.patreon.com/cw/MuscleLove?utm_source=livedoor"
X_LINK = "https://x.com/MuscleGirlLove7"

# --- FANZA(DMM)アフィリエイト（承認済みサイト: musclelove777.livedoor.blog / af_id: pinky2400-003） ---
FANZA_AF_ID = "pinky2400-003"
# al.dmm.co.jp 経由のアフィリ計測リンク。リンク先=FANZAで「腹筋」検索した結果
# （腹筋が際立つ作品が並ぶ。ブログ名「腹筋フェチ」と合致 / af_idで成果計測）
FANZA_LINK = (
    "https://al.dmm.co.jp/?lurl=https%3A%2F%2Fvideo.dmm.co.jp%2Fav%2Flist%2F%3Fkeyword%3D%E8%85%B9%E7%AD%8B"
    f"&af_id={FANZA_AF_ID}&ch=link_tool&ch_id=text"
)
# 記事下部に差し込むFANZA CTAカード（PR表記=ステマ規制対応 / 18禁注記付き / rel=sponsored）
FANZA_BLOCK_HTML = (
    '<div style="text-align:center; background:#2a0a12; padding:20px; border-radius:10px; margin:20px 0;">'
    '<p style="font-size:1.25em; color:#ff4d6d;">🔞 腹筋が際立つ作品をもっと見たい人へ</p>'
    f'<p style="font-size:1.1em;"><a href="{FANZA_LINK}" target="_blank" rel="noopener nofollow sponsored" '
    'style="color:#ff8fa3; text-decoration:underline;">👉 FANZAで腹筋エグい作品をチェック 👈</a></p>'
    '<p style="font-size:0.8em; color:#999;">※18歳未満は閲覧不可 ／ PR（アフィリエイト広告）</p>'
    '</div>'
)
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # ライブドアブログ画像上限: 10MB
UPLOADED_LOG = "uploaded.json"
# 画像ポリシー: この媒体はエロ全開OK（NSFWフィルタなし・2026-07-23ユーザー決定）。
# 微エロ止まりのフィルタが必要なのは hatena-auto-uploader 側。

# --- MuscleLove バックリンクプール（フィットネス系のみ。一般プラットフォーム配慮） ---
ML_BACKLINK_POOL_FITNESS = [
    ("https://musclelove-777.github.io/muscle-meal-girls/", "筋肉女子のマッスルメシ"),
    ("https://musclelove-777.github.io/runners-lab/", "ランナーラボ"),
    ("https://musclelove-777.github.io/armwrestling-girls-navi/", "腕相撲女子ナビ"),
    ("https://musclelove-777.github.io/physique-girls-navi/", "フィジーク女子ナビ"),
    ("https://musclelove-777.github.io/fighting-girls-navi/", "格闘技女子ナビ"),
    ("https://musclelove-777.github.io/joshi-prowrestling-navi/", "女子プロレスナビ"),
    ("https://musclelove-777.github.io/female-physique-queens/", "Female Physique Queens"),
    ("https://musclelove-777.github.io/network/fitness/", "全Fitness Network 15サイト一覧"),
    ("https://musclelove-777.github.io/network/academy/", "MuscleLove Academy 77サイト"),
]


def build_backlink_block():
    """MuscleLoveフィットネス系サイトへのバックリンクHTMLブロックを生成（ランダム3件、冪等マーカー付き）"""
    try:
        k = min(3, len(ML_BACKLINK_POOL_FITNESS))
        selected = random.sample(ML_BACKLINK_POOL_FITNESS, k=k)
        items = " | ".join([f'<a href="{u}" target="_blank" rel="noopener">{n}</a>' for u, n in selected])
        return (
            "\n<br/><br/>\n"
            "<!-- ML_BACKLINK -->\n"
            f'<small style="color:#888;">💡 関連サイト：{items}</small>\n'
            "<!-- /ML_BACKLINK -->\n"
        )
    except Exception:
        return ""

def build_affiliate_block():
    """ボイカツ収益カード（買い物ポイント導線）をsafe_fitnessレーンから1枚挿入。
    content_pool に成果リンク未登録なら空文字＝従来挙動のまま。絶対に死なない。"""
    try:
        from pool_loader import affiliate_card_html
        return affiliate_card_html("safe_fitness", platform="livedoor", k=1)
    except Exception as e:
        print(f"[affiliate] skipped: {e}")
        return ""


# AtomPub API（旧版）ベースURL
ATOM_BASE = "https://livedoor.blogcms.jp/atom/blog/{blog_name}"

# ============================================================
# 記事タイトルテンプレート（ライブドアブログ人気ブログ風）
# 【】付き、数字入り、好奇心を引くスタイル
# ============================================================
TITLE_TEMPLATES = [
    # まとめブログ風（～した結果www / ～がこちら）
    "筋肉女子の写真を見た結果wwwwww",
    "ワイ「筋肉女子とか無いわ」→ 画像を見た結果ｗｗｗ",
    "筋トレ女子のボディがこちらですwww",
    "女さん、鍛えすぎた結果がこちらwww",
    "筋肉女子の最新ショットがこちらになります",
    "ジム通い女子の現在の姿がこちらwww",
    # なんJ・速報系
    "【朗報】筋肉女子、ガチで美しい",
    "【朗報】本日の筋肉美、過去最高を更新",
    "【速報】筋肉女子さん、バキバキすぎるwww",
    "【悲報】ワイ、筋肉女子に完全に堕ちる",
    "【急募】この筋肉美に勝てる画像",
    "【画像】筋トレ女子さん、仕上がりすぎ問題",
    # ～な件・～なんだが系
    "筋肉女子が美しすぎる件について",
    "この筋肉美がヤバすぎる件wwwww",
    "筋トレ女子のボディ、えぐすぎる件",
    "ガチで鍛えた女性の身体がエモすぎる件",
    "筋肉女子を推してるんだが異端か？",
    "この筋肉美見て何も感じないやつおる？",
    # ～すぎて草 / 感嘆系
    "鍛え上げた女性の身体、美しすぎて草",
    "筋肉のカットが芸術的すぎて震えた",
    "マッスル女子のフィジーク、完成度ヤバすぎ",
    "今日の筋肉女子、100点満点で200点",
    "これもう芸術だろ...筋肉女子の肉体美",
    # トレンドワード・バズ系
    "正直この筋肉女子に惚れないやついる？",
    "3秒で惚れる筋肉美がこちら",
    "「筋肉女子 美しい」で検索した結果www",
    "AI超え。これがリアル筋肉美ですけど？",
    "ジム行くモチベ爆上がりする画像貼ってく",
]

# ============================================================
# ブログ記事テンプレート（人気ライブドアブログの書き方 × MuscleLove文体）
# 書き出し → 画像 → 本文 → まとめ → CTA の構成
# ============================================================
#
# ▼ MuscleLove世界観ガイド（テンプレ改訂の芯）
#   ・ビジュアル基準 = ビキニフィットネス競技者の"仕上がり"。ブロンズの大会タン／
#     深く刻まれた腹筋の溝／キラキラのコンテストビキニ／クリアヒール／フェミニンな曲線。
#     血管ギトギトのボディビル寄りではなく、強さ×色気×品の同居。
#   ・スタンス = 推し活。努力の可視化を"愛でる"視点。語彙力を失うほどのリスペクト。
#   ・トーン = 自信高テンション＋エモジ＋日英混在＋ユーモア。ブログは"匂わせ＋煽り"で
#     直接表現はしない（露骨はNG。続きはPatreon/Xへ流す）。closingは下のCTAへ橋渡し。
#
BLOG_BODY_TEMPLATES = [
    # テンプレ1: 煽り×匂わせモード（ギラつき／CTAへ流す）
    {
        'opening': [
            'どうも、MuscleLoveです💪 今日も"筋肉美の沼"へようこそ。',
            'MuscleLoveやで🔥 先に言っとく、今日のは効くぞ。覚悟して見てくれ。',
            'MuscleLoveです。これは保存不可避。心して見るように🔥',
        ],
        'intro': [
            '鍛え抜いた身体って、なんでこんなに目が離せなくなるんやろな。',
            '正直に言う。この一枚、しばらく画面から離れられんかった。',
            'バキバキの腹筋に、フェミニンな曲線。この"矛盾"が最高に刺さる。',
        ],
        'body': [
            'ブロンズに焼けた肌、深く刻まれた腹筋の溝、そこに乗るキラキラのコンテストビキニ。<br>'
            'ステージ照明を弾く肩のライン、絞りきった脚、クリアヒールで決めたポージング。<br><br>'
            'ただ「筋肉がすごい」だけじゃない。<br>'
            '強さと色気と品が、ひとつの身体で同居してる。<br>'
            'これがMuscleLoveの追いかけてる"仕上がり"や。エグいやろ？',

            '見てくれ、この絞り。<br>'
            '腹筋の一本一本、肩のキャップ、キュッと締まったウエストからの脚のライン。<br>'
            'ぜんぶ計算され尽くした美のバランスや。<br><br>'
            'ここまで作り込むのに、どれだけの朝と、どれだけの我慢があったか。<br>'
            'その物語ごと"美しい"んよな。',
        ],
        'closing': [
            'で、ここから先は……大人の時間や。続きは下のリンクで🔞',
            'この角度で「うわ」ってなった人、絶対に俺と気が合う。ベストショットはPatreonで待ってる👇',
            '表で出せるのはここまで。本気の一枚は向こう側にある。下のリンク、押してこ👇',
        ],
    },
    # テンプレ2: 世界観・哲学モード（"なぜ美しいか"を語る）
    {
        'opening': [
            'MuscleLoveです✨ 今日は"筋肉美の哲学"を1枚と一緒に。',
            'こんにちは、MuscleLoveです。今日も最高の一枚から始めよう💪',
        ],
        'intro': [
            '筋肉女子がなぜこんなに美しいのか、俺なりの答えを話させてくれ。',
            '鍛えた女性の身体って、ただの"体型"じゃない。生き方が出るんよ。',
            'この一枚を見ながら、筋肉美の本質を語りたい。',
        ],
        'body': [
            '■ 筋肉は"努力の可視化"<br><br>'
            '毎朝の計量、味気ない鶏むね、ラスト1レップの震え。<br>'
            'その全部が、腹筋の溝と肩の丸みに刻まれていく。<br>'
            '嘘がつけない。だから、こんなに美しい。<br><br>'
            'そして仕上げがすごい。ブロンズの大会タン、ラメの効いたビキニ、'
            'クリアヒール——ストイックさの上に"華"を乗せてくる。<br>'
            'この強さと可愛さの二段構えが、MuscleLoveの推しポイントや。',

            '■ 強さと色気は、共存する<br><br>'
            '「筋肉＝ゴツい」なんて誰が決めた。<br>'
            'バキバキに絞った身体に、フェミニンな曲線とやわらかい笑顔。<br>'
            'この振れ幅こそが、鍛えた女性だけが到達できる領域なんよ。<br><br>'
            '今日の一枚も、まさにその"到達点"。<br>'
            '見れば見るほど、細部に努力の跡が見えてくるはず。',
        ],
        'closing': [
            'この美学、ちょっとでも伝わったら嬉しい。もっと深いやつは公式で公開中💪',
            '筋肉美の世界、まだ入口。ここから先はPatreon/Xでどうぞ👇',
            '"強くて美しい"を、これからも全力で発信していく。ついてきてな🔥',
        ],
    },
    # テンプレ3: ストーリー・シチュエーションモード（没入させる）
    {
        'opening': [
            'MuscleLoveです🔥 今日はちょっと想像してみてほしい。',
            'どうも、MuscleLoveです💪 一枚から物語を感じる日ってあるよな。',
        ],
        'intro': [
            'ステージ袖。照明が当たった瞬間、会場の空気が変わる——そんな一枚。',
            'ジム終わり、鏡の前で決めたであろうこのポーズ。想像すると熱いよな。',
            'ホテルの一室、差し込む光。仕上がった身体が、静かに"完成"を語ってる。',
        ],
        'body': [
            'ステージに立つ数分のために、彼女は何ヶ月も自分を追い込んできた。<br>'
            '水を抜き、糖質を操作し、限界まで絞る。<br>'
            'そして本番、ブロンズの肌にラメのビキニをまとって、軽やかにポーズを決める。<br><br>'
            '深く割れた腹筋、盛り上がった肩、伸びた背筋、クリアヒールで魅せる脚。<br>'
            '苦しさは一切見せず、ただ美しく微笑む。<br>'
            'かっこよすぎて、こっちが背筋伸びるわ。',

            '想像してくれ。目の前にこの肉体美があったら、な。<br>'
            'シュレッドされた腹筋、絞りきったウエスト、フェミニンな曲線。<br>'
            '"強さ"と"色気"が同じ身体で殴りかかってくる。<br><br>'
            '「人間の身体って、ここまで美しくなれるんだ」——<br>'
            '毎回そう思わされる。これがMuscleLoveの見てる景色や。',
        ],
        'closing': [
            'こういう一枚に出会えるから、推し活はやめられん。続きは下で🔥',
            '物語の"その先"は、Patreon/Xに置いてある。会いに来てな👇',
            '今日もいい景色を見られた。もっと濃いのは公式で待ってる💪',
        ],
    },
    # テンプレ4: ビジュアル重視モード（短いが世界観を宿す）
    {
        'opening': [
            'MuscleLove💪🔥',
            '🔥今日のベスト、いくで🔥',
        ],
        'intro': [
            'はい、語る前に——まず見て。',
            '今日の一枚。理屈は後や。',
            'これは、ちょっと言葉にならん。',
        ],
        'body': [
            'ブロンズの肌。<br>'
            '割れた腹筋。<br>'
            'ラメのビキニ。<br>'
            'クリアヒール。<br><br>'
            '"強い×美しい"の完成形。語彙力、いらん。',

            '絞り、キレ、艶。<br>'
            'そこにフェミニンな曲線。<br>'
            '甘えゼロで、この可愛さ。<br><br>'
            'これぞMuscleLoveの理想形。反則やろ。',

            '努力が、形になった。<br>'
            'それ以上の説明が必要か？<br><br>'
            '筋肉女子、推すしかない。優勝🏆',
        ],
        'closing': [
            'この一枚で刺さった人へ。続きは下に置いといた👇',
            'ベストショットはPatreon/Xで。取りに来てな🔥',
            '表はここまで。本気の続きは向こうで💪',
        ],
    },
    # テンプレ5: 推し活・読者参加モード
    {
        'opening': [
            'MuscleLoveです！今日はみんなに聞きたい💪',
            'こんにちは、MuscleLoveです✨ 一緒に語ろうや。',
        ],
        'intro': [
            '突然やけど、筋肉女子の"どこ"に一番惚れる？',
            'この一枚を見て、あなたの推しポイントを教えてほしい。',
            '筋肉美の沼、どこから落ちた？俺は腹筋の溝からやった。',
        ],
        'body': [
            '俺の推しは、やっぱりこの"仕上がりの物語"や。<br><br>'
            'ブロンズの大会タン、割れた腹筋、ラメのビキニ、クリアヒール。<br>'
            '華やかに見えるけど、その裏には計量とトレの積み重ねがある。<br>'
            '努力と結果が同じ身体に乗ってるの、最高にエモくない？<br><br>'
            'あなたはどこ派？肩？背中？脚？それとも笑顔？',

            '「強さ」と「色気」、両方あるのが筋肉女子の反則ポイント。<br><br>'
            'バキバキに絞った身体に、フェミニンな曲線と自信の笑顔。<br>'
            'この振れ幅にやられる人、絶対に多いはず。<br>'
            '俺は完全にこっち側の人間や。<br><br>'
            'あなたの"沼落ちの瞬間"、コメントで聞かせて🔥',
        ],
        'closing': [
            'コメントで語ろう！そしてもっと濃いのは公式で公開中👇',
            'あなたの推しポイント、教えて。ベストショットはPatreon/Xに🔥',
            'この沼、一緒に深めていこ。続きは下のリンクから💪',
        ],
    },
]

# ============================================================
# 日常系コンテキスト（記事冒頭の雑談パート＝日記ブログ感を出す）
# ・固有名詞なしの汎用日常ネタのみ（ジム/メシ/サウナ/季節）
# ・context/daily_context.md があれば「- 」行を追加ネタとして取り込む
#   （自動運営ループや手動で差し替え可能。公開safeな内容だけ書くこと）
# ============================================================

DAILY_TOPICS_COMMON = [
    '最近はトレ後のプロテインを水じゃなくて無調整豆乳で割るのにハマってる。腹持ちが全然違う。',
    'ジムで顔なじみの常連さんが増えてきた。無言の会釈だけの関係、なんか心地いいんよな。',
    '深夜のコンビニで高タンパク系の新作スイーツを見つけて迷わず確保。罪悪感ゼロのご褒美、最高。',
    'サウナ→水風呂→外気浴のループが最近の整いルーティン。トレ後にやると回復が段違い。',
    '朝イチの空腹有酸素を試してるんやけど、終わったあとの朝メシが美味すぎて続いてる。',
    '鶏むね肉の低温調理をマスターしつつある。パサパサ時代にはもう戻れん。',
    '睡眠を7時間確保するようにしたら扱う重量がじわっと伸びた。結局、寝るのが最強のサプリや。',
    '休みの日は散歩がてら1万歩。歩きながら次のブログネタを考えるのが日課になってる。',
    '卵を1日3個食べる生活を続けてたら、ゆで卵の殻むきだけ異様に上手くなったｗ',
    'イヤホンを新調したらトレのテンションが別物になった。音楽は合法のブースターやと思う。',
    '最近の定食屋メシは塩サバ定食が優勝し続けてる。脂質は気になるけど美味さが勝つ。',
    '寝る前5分だけストレッチ始めたら朝の身体の軽さが違う。継続って偉大やな。',
]

DAILY_TOPICS_BY_SEASON = {
    # 3-5月
    'spring': [
        '春は花見がてらのウォーキングが捗る。外トレが気持ちいい季節になってきた。',
        '新生活シーズンでジムに新顔が増えてきた。初心を思い出させてもらってる。',
    ],
    # 6-8月
    'summer': [
        '夏本番でジムの冷房がありがたい季節。外気温とのギャップで滝汗かいてる。',
        '暑すぎて麦茶を1日2リットル飲んでる。水分補給もトレのうちや。',
        '夏は薄着の季節やからか、ジム全体の気合がいつもより高い。みんな仕上げてきてる。',
    ],
    # 9-11月
    'autumn': [
        '食欲の秋、増量シーズン到来。米が美味すぎて計量が仕事になってきた。',
        '涼しくなって外ランが復活。秋の朝の空気は完全にご褒美。',
    ],
    # 12-2月
    'winter': [
        '寒すぎてウォームアップが長くなる季節。でも冬のトレ後の風呂は優勝や。',
        '年末年始の食事量を筋肉に変換する予定（希望的観測）。',
    ],
}

DAILY_BRIDGE_LINES = [
    '——って感じの近況はさておき。今日の一枚、いこか🔥',
    'まあそんな日常はここまでにして、本題や💪',
    '…はい、雑談終わり。今日も最高のやつ持ってきたで🔥',
    'そんな毎日を送りつつ、今日もこの時間がやってきた💪',
]

DAILY_CONTEXT_FILE = os.path.join('context', 'daily_context.md')


def load_extra_daily_topics():
    """context/daily_context.md の「- 」行を追加の日常ネタとして読み込む（無ければ空）"""
    try:
        if not os.path.exists(DAILY_CONTEXT_FILE):
            return []
        with open(DAILY_CONTEXT_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        return [ln[2:].strip() for ln in lines if ln.startswith('- ') and len(ln.strip()) > 5]
    except Exception:
        return []


def build_daily_block():
    """冒頭の日常雑談パート（1ネタ＋本題への橋渡し）を生成"""
    try:
        month = datetime.datetime.now().month
        season = ('winter' if month in (12, 1, 2) else
                  'spring' if month in (3, 4, 5) else
                  'summer' if month in (6, 7, 8) else 'autumn')
        pool = DAILY_TOPICS_COMMON + DAILY_TOPICS_BY_SEASON.get(season, []) + load_extra_daily_topics()
        if not pool:
            return ''
        topic = random.choice(pool)
        bridge = random.choice(DAILY_BRIDGE_LINES)
        return f'<p>{topic}<br>{bridge}</p>\n\n'
    except Exception:
        return ''


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
    """Google Driveフォルダから画像を1枚だけ取得する。

    旧実装はフォルダ全体(1000枚超)を毎回DLしてGoogleのレート制限
    ("Cannot retrieve the public link ... have had many accesses")に
    当たり、途中で中断→未投稿画像が取れず空振りしていた。
    対策として以下に変更:
      1) skip_download=True でファイル一覧(id/path)だけ取得（本体DLしない）
      2) 画像拡張子 & 未投稿のものから候補を作る
      3) その候補から1枚だけ gdown.download でDL（失敗時は別候補を最大5回試行）
    """
    dl_dir = "media"
    os.makedirs(dl_dir, exist_ok=True)
    url = f"https://drive.google.com/drive/folders/{GDRIVE_FOLDER_ID}"
    print(f"Listing Google Drive folder (no bulk download): {url}")

    try:
        listing = gdown.download_folder(
            url, output=dl_dir, quiet=True, remaining_ok=True, skip_download=True
        )
    except Exception as e:
        print(f"Folder listing error: {e}")
        return []

    if not listing:
        print("Folder listing returned 0 entries")
        return []

    # 画像ファイルのみ抽出: (file_id, basename, local_path)
    candidates = []
    for f in listing:
        path = getattr(f, "path", None) or getattr(f, "local_path", None) or ""
        fid = getattr(f, "id", None)
        local_path = getattr(f, "local_path", None) or os.path.join(dl_dir, os.path.basename(path))
        ext = os.path.splitext(path)[1].lower()
        if fid and ext in IMAGE_EXTENSIONS:
            candidates.append((fid, os.path.basename(path), local_path))

    print(f"Total image files in Drive: {len(candidates)}")
    if not candidates:
        return []

    # 未投稿のみ（UPLOAD_ALL のときは全候補）
    if os.environ.get("UPLOAD_ALL", "").lower() in ("1", "true", "yes"):
        pool = candidates[:]
    else:
        log_data = load_uploaded_log()
        uploaded_names = {
            (entry["file"] if isinstance(entry, dict) else entry)
            for entry in log_data.get("files", [])
        }
        pool = [c for c in candidates if c[1] not in uploaded_names]

    print(f"Unposted candidates: {len(pool)} / Total images: {len(candidates)}")
    if not pool:
        return []

    # 未投稿から1枚だけDL（失敗したら別候補で最大5回リトライ）
    random.shuffle(pool)
    for fid, fname, local_path in pool[:5]:
        try:
            os.makedirs(os.path.dirname(local_path) or dl_dir, exist_ok=True)
            out = gdown.download(id=fid, output=local_path, quiet=False)
            if out and os.path.exists(out):
                size = os.path.getsize(out)
                if size <= MAX_FILE_SIZE:
                    print(f"Downloaded: {fname} ({size / 1024 / 1024:.1f}MB)")
                    return [out]
                print(f"Skip (>10MB): {fname} ({size / 1024 / 1024:.1f}MB)")
            else:
                print(f"Download returned no file: {fname}")
        except Exception as e:
            print(f"Single download failed ({fname}): {e} -- trying next")
    print("All download attempts failed")
    return []


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


# ============================================================
# 画像コンテキスト抽出（ファイル名の生成タグ→日本語シチュエーション）
# 例: "{{{night pool}}}, {{{outside}}}, muscular gyaru woman, glam s-xxx.png"
#     → 舞台:[夜プール, 屋外] / 見どころ:[ギャル系, バキバキ筋肉]
# ============================================================

SCENE_PLACE_JP = [
    ('night pool', '夜プール'),
    ('pool', 'プール'),
    ('beach', 'ビーチ'),
    ('onsen', '温泉'),
    ('hot spring', '温泉'),
    ('gym', 'ジム'),
    ('hotel', 'ホテルの一室'),
    ('bedroom', 'ホテルの一室'),
    ('rooftop', 'ルーフトップ'),
    ('outside', '屋外'),
    ('outdoor', '屋外'),
    ('city', '街中'),
    ('stage', 'ステージ'),
    ('locker', 'ロッカールーム'),
]

SCENE_FEATURE_JP = [
    ('micro bikini', 'マイクロビキニ'),
    ('string bikini', 'ひもビキニ'),
    ('bikini', 'ビキニ'),
    ('swimsuit', '水着'),
    ('dark tanned', 'こんがり焼けた褐色肌'),
    ('tanned', '日焼け肌'),
    ('gyaru', 'ギャル系'),
    ('japanese gal', 'ギャル系'),
    ('show armpit', 'ワキ見せポーズ'),
    ('armpit', 'ワキ見せポーズ'),
    ('from side', '横からのアングル'),
    ('from behind', '後ろ姿'),
    ('from above', '俯瞰アングル'),
    ('kneeling', '膝立ちポーズ'),
    ('lying', '寝そべりポーズ'),
    ('squat', 'スクワット'),
    ('flex', 'フレックスポーズ'),
    ('double biceps', 'ダブルバイセップス'),
    ('abs', 'バキバキ腹筋'),
    ('six pack', 'バキバキ腹筋'),
    ('ripped', '絞りきった仕上がり'),
    ('muscular', '鍛え抜いた筋肉'),
    ('glamorous', 'グラマラス'),
    ('wet', '濡れ髪・濡れ肌'),
    ('oil', 'オイル肌'),
    ('smile', '自信の笑顔'),
]


def extract_scene_context(file_path):
    """ファイル名/パスから (舞台リスト, 見どころリスト) を抽出。無名ファイルなら空。

    マッチした語は文字列から消費し、部分語の二重マッチを防ぐ
    （例: "night pool" が 'night pool' と 'pool' の両方に当たらないように。
    テーブルは長いキー順に並べてあることが前提）。"""
    s = str(file_path).lower().replace('\\', '/').replace('_', ' ').replace('-', ' ')
    places, feats = [], []
    for key, jp in SCENE_PLACE_JP:
        if key in s:
            s = s.replace(key, ' ')
            if jp not in places:
                places.append(jp)
    for key, jp in SCENE_FEATURE_JP:
        if key in s:
            s = s.replace(key, ' ')
            if jp not in feats:
                feats.append(jp)
    return places[:2], feats[:3]


SCENE_LEAD_TEMPLATES = [
    '今日の舞台は【{place}】。この世界観、まずは浴びてくれ。',
    '本日のシチュエーションは【{place}】。ロケーション込みで"作品"や。',
    '【{place}】での一枚。この空気感がたまらん。',
]

SCENE_FEATURE_TEMPLATES = [
    '見どころは「{feats}」。細部まで世界観が仕上がってる。',
    '注目ポイントは「{feats}」。わかる人にはわかるやつ。',
    '今日の推し要素は「{feats}」。この組み合わせは反則。',
]


def build_scene_block(file_path):
    """画像固有のシチュエーション段落を生成（タグが無ければ空文字で従来挙動）"""
    places, feats = extract_scene_context(file_path)
    lines = []
    if places:
        tpl = random.choice(SCENE_LEAD_TEMPLATES)
        lines.append(tpl.format(place=' × '.join(places)))
    if feats:
        tpl = random.choice(SCENE_FEATURE_TEMPLATES)
        lines.append(tpl.format(feats=' × '.join(feats)))
    if not lines:
        return ''
    return '<p>' + '<br>'.join(lines) + '</p>\n\n<p>&nbsp;</p>\n\n'


def sanitize_category(name, max_len=30):
    """フォルダ名からカテゴリ名を安全に抽出"""
    name = re.sub(r'(?i)\s*not\s+nsfw\s*', ' ', name).strip()
    name = re.sub(r'[{}\[\]]', '', name)
    if ',' in name:
        name = name.split(',')[0].strip()
    name = name.strip(' -_')
    if len(name) > max_len:
        name = name[:max_len].rstrip(' -_')
    return name if name else "Muscle"


# アダルト区分マーカー（元画像のDriveフォルダ名/ファイル名から推定）。
# 安全マーカー（not nsfw / sfw / safe / 健全）が最優先で False。
_NSFW_SAFE_RE = re.compile(r'(?i)(not[\s_\-]*nsfw|\bsfw\b|\bsafe\b|健全|着衣のみ)')
_NSFW_HIT_RE = re.compile(r'(?i)(nsfw|nude|naked|topless|hadaka|全裸|ヌード|トップレス|18禁|r-?18|adult|エロ)')


def is_nsfw_source(file_path):
    """元画像のパス（Driveフォルダ名/ファイル名）にアダルト区分マーカーがあるか。

    タイトルの「NSFW - 」接頭辞は本来カテゴリ（フォルダ名）依存で、
    (1) NSFW以外の名前のフォルダに入ったヌード、(2) 50字超で接頭辞脱落、
    の2経路で無警告公開が起きていた。この関数で元パスを直接見て判定し、
    呼び出し側で確実に接頭辞を付けるための保険にする。
    'not nsfw' 等の安全マーカーがあれば False を優先。判定不能は False。
    ※注意: マーカーの無いフォルダに置かれたヌードは検知できない。
      恒久対策は「ヌードは必ず NSFW 名のフォルダへ」という元データ側の運用徹底。
    """
    p = (file_path or "").replace('\\', '/')
    if _NSFW_SAFE_RE.search(p):
        return False
    return bool(_NSFW_HIT_RE.search(p))


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

    # 画像固有のシチュエーション段落（タグ無しファイルなら空）
    scene_html = build_scene_block(file_path)
    # 冒頭の日常雑談パート（日記ブログ感）
    daily_html = build_daily_block()

    hashtag_html = ' '.join([f'#{t}' for t in tags[:15]])

    html = f'''<p>{opening}</p>

{daily_html}<p>{intro}</p>

<p>&nbsp;</p>

<div style="text-align: center;">
<p><img src="{image_url}" alt="{category}" style="max-width: 100%;" /></p>
</div>

<p>&nbsp;</p>

{scene_html}<p>{body}</p>

<p>&nbsp;</p>

<p>{closing}</p>

<hr />

<!-- ML_SNS_CTA -->
<div style="text-align: center; background: #1a1a2e; padding: 20px; border-radius: 10px; margin: 20px 0;">
<p style="font-size: 1.3em; color: #FFD700;">🔥 もっと見たい？ 公式SNSで毎日更新中！</p>
<p style="font-size: 1.1em;"><a href="{PATREON_LINK}" target="_blank" rel="noopener" style="color: #00C9FF; text-decoration: underline;">
👉 MuscleLove on Patreon（限定コンテンツ公開中）👈
</a></p>
<p style="font-size: 1.1em;">🐦 <a href="{X_LINK}" target="_blank" rel="noopener" style="color: #1DA1F2; text-decoration: underline;">
X（旧Twitter）@MuscleGirlLove7 でほぼ毎日更新中！
</a></p>
<p style="font-size: 0.9em; color: #ccc;">ここでしか見れない筋肉美をお届け中💪</p>
</div>
<!-- /ML_SNS_CTA -->

{FANZA_BLOCK_HTML}

<p>&nbsp;</p>

<p style="color: #888; font-size: 0.85em;">{hashtag_html}</p>'''

    html = html.rstrip() + build_affiliate_block() + build_backlink_block()
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

    # シチュエーションタグをハッシュタグへも反映（先頭に挿入して確実に表示枠へ）
    places, feats = extract_scene_context(selected)
    for t in reversed(places + feats):
        if t not in tags:
            tags.insert(0, t)

    # タイトル生成（舞台タグがあれば【夜プール】等を冠して具体性を出す）
    template = random.choice(TITLE_TEMPLATES)
    if places:
        title = f"【{places[0]}】{template}"
    elif category != "Muscle":
        title = f"{category} - {template}"
    else:
        title = template
    if len(title) > 50:
        title = template

    # NSFW警告の付与（保険）: 元画像のフォルダ/ファイル名がアダルト区分なら、
    # カテゴリ由来の接頭辞が付いていなくても（50字超で脱落しても）必ず先頭に付ける。
    # これで「ヌードなのに無警告公開」を防ぐ。既にNSFW始まりなら二重付与しない。
    if is_nsfw_source(selected) and not title.lstrip().upper().startswith("NSFW"):
        title = f"NSFW - {template}"

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
