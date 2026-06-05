# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "Pillow>=10.0",
#   "Janome>=0.5",
# ]
# ///
"""日本語テキストを漫画風の吹き出し画像（背景透過 PNG）にして出力する。

使い方の例（デフォルトは縦書き・手書き風・文節で自動改行）:
    uv run speech_bubble.py "今日はいい天気ですね、散歩に行きましょう。"
    uv run speech_bubble.py "やったー！" -o out.png --shape ellipse
    uv run speech_bubble.py "なるほど…" --horizontal --shape jagged
    uv run speech_bubble.py "だめだ！" --tail bottom-left --font-size 64
    uv run speech_bubble.py "手書き風！" --shape hand --seed 3
    uv run speech_bubble.py "こっち！" --shape hand --tail-clock 1.5
    uv run speech_bubble.py "好きな書体で" --font /path/to/font.otf
    uv run speech_bubble.py "強調！" --bold
    uv run speech_bubble.py "行数を指定" --lines 3
    uv run speech_bubble.py "自動改行を切る" --no-auto-wrap --max-chars 5
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# フォント
# ---------------------------------------------------------------------------
# 太めの Noto Sans CJK を漫画らしさのため優先。見つからない順に fallback。
FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", 0),
]

# 縦書きで 90 度回転させたい記号（長音符・各種ダッシュ・括弧など）
VERTICAL_ROTATE = set("ー－—–~〜（）()「」『』【】〔〕[]｛｝{}…‥")
# 縦書きで右上に寄せたい小書き文字・約物
VERTICAL_SHIFT = set("、。，．")


def load_font(size: int, font_path: str | None = None, index: int = 0) -> ImageFont.FreeTypeFont:
    # --font が指定されていればそれを優先
    if font_path:
        if not Path(font_path).exists():
            raise SystemExit(f"フォントファイルが見つかりません: {font_path}")
        try:
            return ImageFont.truetype(font_path, size=size, index=index)
        except OSError as e:
            raise SystemExit(f"フォントを読み込めませんでした: {font_path} ({e})")
    # 未指定ならシステムの日本語フォントを自動検出
    for path, idx in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size, index=idx)
    raise SystemExit(
        "日本語フォントが見つかりませんでした。--font でファイルを指定するか、"
        "Noto Sans CJK 等を入れてください。\n"
        "  Debian/Ubuntu: sudo apt install fonts-noto-cjk"
    )


# ---------------------------------------------------------------------------
# テキストの折り返しとサイズ計算
# ---------------------------------------------------------------------------
def char_size(font: ImageFont.FreeTypeFont, ch: str) -> tuple[int, int]:
    box = font.getbbox(ch)
    return box[2] - box[0], box[3] - box[1]


def wrap_horizontal(text: str, max_chars: int) -> list[str]:
    """明示的な改行を尊重しつつ、max_chars で折り返す。"""
    lines: list[str] = []
    for raw in text.split("\n"):
        if not raw:
            lines.append("")
            continue
        for i in range(0, len(raw), max_chars):
            lines.append(raw[i : i + max_chars])
    return lines


def wrap_vertical(text: str, max_chars: int) -> list[str]:
    """縦書き用：改行を列の区切りとして扱い、長い行は折り返す。"""
    return wrap_horizontal(text, max_chars)


# ---------------------------------------------------------------------------
# テキスト描画
# ---------------------------------------------------------------------------
def draw_horizontal(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    cx: int,
    cy: int,
    line_gap: float,
    fill: tuple,
    stroke: int = 0,
) -> None:
    ascent, descent = font.getmetrics()
    line_h = (ascent + descent) * line_gap
    total_h = line_h * len(lines)
    y = cy - total_h / 2 + (line_h - (ascent + descent)) / 2
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text((cx - w / 2, y), line, font=font, fill=fill,
                  stroke_width=stroke, stroke_fill=fill)
        y += line_h


def draw_vertical(
    draw: ImageDraw.ImageDraw,
    columns: list[str],
    font: ImageFont.FreeTypeFont,
    cx: int,
    cy: int,
    char_gap: float,
    col_gap: float,
    fill: tuple,
    stroke: int = 0,
) -> None:
    size = font.size
    cell = size * char_gap
    col_w = size * col_gap
    n_cols = len(columns)
    max_rows = max((len(c) for c in columns), default=1)

    total_w = col_w * n_cols
    total_h = cell * max_rows
    # 縦書きは右の列から左へ
    x_start = cx + total_w / 2 - col_w / 2
    y_start = cy - total_h / 2

    for ci, col in enumerate(columns):
        x = x_start - ci * col_w
        y = y_start
        for ch in col:
            cw, chh = char_size(font, ch)
            if ch in VERTICAL_ROTATE:
                # 1 文字を回転描画
                pad = size + stroke * 2
                tmp = Image.new("RGBA", (pad, pad), (0, 0, 0, 0))
                td = ImageDraw.Draw(tmp)
                bb = font.getbbox(ch)
                td.text((-bb[0] + stroke, -bb[1] + stroke), ch, font=font, fill=fill,
                        stroke_width=stroke, stroke_fill=fill)
                tmp = tmp.rotate(-90, expand=False)
                draw._image.paste(tmp, (int(x - pad / 2 + cell / 2), int(y)), tmp)
            else:
                dx = x - cw / 2
                dy = y + (cell - chh) / 2
                if ch in VERTICAL_SHIFT:
                    dx += cell * 0.25
                    dy -= cell * 0.25
                bb = font.getbbox(ch)
                draw.text((dx - bb[0], dy - bb[1]), ch, font=font, fill=fill,
                          stroke_width=stroke, stroke_fill=fill)
            y += cell


# ---------------------------------------------------------------------------
# 吹き出しの形
# ---------------------------------------------------------------------------
def tail_polygon(
    bounds: tuple[float, float, float, float],
    direction: str,
    scale: float,
) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w, h = x1 - x0, y1 - y0
    tw = min(w, h) * 0.22 * scale          # しっぽの根元の幅
    tl = min(w, h) * 0.225 * scale         # しっぽの長さ

    if direction.startswith("bottom"):
        base_y = y1 - 1
        if direction.endswith("left"):
            bx = x0 + w * 0.30
            tip = (bx - tl * 0.4, base_y + tl)
        elif direction.endswith("right"):
            bx = x0 + w * 0.70
            tip = (bx + tl * 0.4, base_y + tl)
        else:
            bx = cx
            tip = (bx, base_y + tl)
        return [(bx - tw / 2, base_y), (bx + tw / 2, base_y), tip]
    if direction.startswith("top"):
        base_y = y0 + 1
        bx = cx if direction == "top" else (x0 + w * (0.30 if "left" in direction else 0.70))
        tip = (bx, base_y - tl)
        return [(bx - tw / 2, base_y), (bx + tw / 2, base_y), tip]
    if direction == "left":
        base_x = x0 + 1
        return [(base_x, cy - tw / 2), (base_x, cy + tw / 2), (base_x - tl, cy)]
    if direction == "right":
        base_x = x1 - 1
        return [(base_x, cy - tw / 2), (base_x, cy + tw / 2), (base_x + tl, cy)]
    return []


# 時計の文字盤と同じ向き（12=上, 3=右, 6=下, 9=左）。小数も可。
def clock_to_theta(hour: float) -> float:
    """時計の時間(1-12)を、真上から時計回りに測った角度[rad]に変換。"""
    return (hour % 12) / 12.0 * 2 * math.pi


def angular_tail_polygon(
    bounds: tuple[float, float, float, float],
    theta: float,
    scale: float,
) -> list[tuple[float, float]]:
    """楕円の境界上から角度 theta 方向へ外向きに生やすしっぽ。任意角度に対応。"""
    x0, y0, x1, y1 = bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    w, h = x1 - x0, y1 - y0
    tw = min(w, h) * 0.22 * scale
    tl = min(w, h) * 0.225 * scale

    dx, dy = math.sin(theta), -math.cos(theta)        # 外向きの単位ベクトル
    # 中心から dx,dy 方向に伸ばした線が楕円と交わる点（しっぽの付け根）
    t = 1.0 / math.hypot(dx / rx, dy / ry)
    bx, by = cx + dx * t, cy + dy * t
    px, py = -dy, dx                                  # 付け根の幅方向（接線）
    return [(bx + px * tw / 2, by + py * tw / 2),
            (bx - px * tw / 2, by - py * tw / 2),
            (bx + dx * tl, by + dy * tl)]


# 名前付きの向き → 吹き出しを寄せる方向（しっぽ用の余白を作るためだけの近似ベクトル）
NAMED_TAIL_DIR = {
    "bottom": (0.0, 1.0), "bottom-left": (-0.45, 1.0), "bottom-right": (0.45, 1.0),
    "top": (0.0, -1.0), "top-left": (-0.45, -1.0), "top-right": (0.45, -1.0),
    "left": (-1.0, 0.0), "right": (1.0, 0.0),
}


def resolve_tail(args, bounds):
    """しっぽの点列 [base0, base1, tip] と外向き単位ベクトルを返す。無しなら ([], None)。"""
    if args.tail_clock is not None:
        theta = clock_to_theta(args.tail_clock)
        pts = angular_tail_polygon(bounds, theta, args.tail_scale)
        return pts, (math.sin(theta), -math.cos(theta))
    if args.tail == "none":
        return [], None
    pts = tail_polygon(bounds, args.tail, args.tail_scale)
    dx, dy = NAMED_TAIL_DIR[args.tail]
    n = math.hypot(dx, dy)
    return pts, (dx / n, dy / n)


def draw_bubble(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[float, float, float, float],
    shape: str,
    fill: tuple,
    outline: tuple,
    line_width: int,
    tail_pts: list,
    opts: dict,
) -> None:
    x0, y0, x1, y1 = bounds

    if shape == "hand":
        # 手描き風は専用ルーチンで本体もしっぽもまとめて描く
        draw_hand_bubble(draw, bounds, fill, outline, line_width, tail_pts,
                         opts["seed"], opts["wobble"], opts["strokes"])
        return

    # しっぽ（先に塗って、本体の縁取りで根元を隠す）
    if tail_pts:
        draw.polygon(tail_pts, fill=fill, outline=outline, width=line_width)

    if shape == "ellipse":
        draw.ellipse(bounds, fill=fill, outline=outline, width=line_width)
    elif shape == "rounded":
        r = min(x1 - x0, y1 - y0) * 0.28
        draw.rounded_rectangle(bounds, radius=r, fill=fill, outline=outline, width=line_width)
    elif shape == "rectangle":
        draw.rectangle(bounds, fill=fill, outline=outline, width=line_width)
    elif shape in ("jagged", "burst"):
        draw.polygon(
            burst_points(bounds, spikes=16, jag=0.18 if shape == "jagged" else 0.32),
            fill=fill,
            outline=outline,
            width=line_width,
        )
    else:
        raise SystemExit(f"未知の shape: {shape}")

    # しっぽ本体を再度塗って、本体の輪郭線で消えた部分の塗りを補完
    if tail_pts:
        draw.polygon(tail_pts, fill=fill)
        # しっぽの 2 辺だけ輪郭を引き直す（根元の辺は引かない）
        draw.line([tail_pts[0], tail_pts[2]], fill=outline, width=line_width, joint="curve")
        draw.line([tail_pts[1], tail_pts[2]], fill=outline, width=line_width, joint="curve")


def burst_points(bounds, spikes: int, jag: float) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    pts = []
    for i in range(spikes * 2):
        ang = math.pi * i / spikes
        r = 1.0 if i % 2 == 0 else (1.0 - jag)
        pts.append((cx + math.cos(ang) * rx * r, cy + math.sin(ang) * ry * r))
    return pts


# ---------------------------------------------------------------------------
# 手書き風の輪郭
# ---------------------------------------------------------------------------
def _harmonics(rng: random.Random, wobble: float, ks=(2, 3, 4, 5, 7)):
    """閉曲線になる低周波の揺らぎ成分（k は整数なので一周して必ず閉じる）。"""
    return [(k, wobble * rng.uniform(0.25, 1.0) / k, rng.uniform(0, 2 * math.pi))
            for k in ks]


def hand_outline(
    bounds: tuple[float, float, float, float],
    rng: random.Random,
    wobble: float,
    n: int = 220,
) -> list[tuple[float, float]]:
    """楕円の輪郭を低周波ノイズで揺らした、手描き風の閉じた点列。"""
    x0, y0, x1, y1 = bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    radial = _harmonics(rng, wobble)               # 半径方向の揺らぎ
    # 中心も少しだけ動かして「描き始めのズレ」を演出
    dcx, dcy = rng.uniform(-1, 1) * rx * 0.02, rng.uniform(-1, 1) * ry * 0.02
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        rr = 1.0 + sum(a * math.sin(k * t + ph) for k, a, ph in radial)
        pts.append((cx + dcx + math.cos(t) * rx * rr,
                    cy + dcy + math.sin(t) * ry * rr))
    return pts


def sketchy_stroke(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    color: tuple,
    width: int,
    strokes: int,
    rng: random.Random,
    jitter: float,
    close: bool = True,
) -> None:
    """同じパスを毎回わずかにズラして重ね描きし、ペン入れ風の線にする。"""
    for _ in range(strokes):
        # 各点に低周波オフセットを加える（x, y で別位相）
        ph_x, ph_y = rng.uniform(0, 6.28), rng.uniform(0, 6.28)
        kx, ky = rng.choice((2, 3)), rng.choice((2, 3))
        amp = jitter
        shifted = []
        m = len(pts)
        for i, (x, y) in enumerate(pts):
            t = 2 * math.pi * i / m
            shifted.append((x + amp * math.sin(kx * t + ph_x),
                            y + amp * math.cos(ky * t + ph_y)))
        line = shifted + [shifted[0]] if close else shifted
        draw.line(line, fill=color, width=width, joint="curve")


def draw_hand_bubble(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[float, float, float, float],
    fill: tuple,
    outline: tuple,
    line_width: int,
    tail_pts: list,
    seed: int,
    wobble: float,
    strokes: int,
) -> None:
    rng = random.Random(seed)
    w = min(bounds[2] - bounds[0], bounds[3] - bounds[1])
    # 揺らぎ量は本体サイズ基準（小さな画像でも大きな画像でも同じ印象に）
    body_wobble = wobble * 0.05
    jitter = max(1.0, w * 0.004)

    body = hand_outline(bounds, rng, body_wobble)
    # 本体としっぽを 1 本の連続した輪郭に合成（付け根に境界線が出ない）
    path = merge_tail(body, bounds, tail_pts)

    # 塗りと、ペン入れ風の一筆書き輪郭
    draw.polygon(path, fill=fill)
    sketchy_stroke(draw, path, outline, line_width, strokes, rng, jitter, close=True)


def merge_tail(
    body: list[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    tail_pts: list,
) -> list[tuple[float, float]]:
    """本体輪郭のしっぽ付け根区間を取り除き、しっぽの先端へ迂回させた一本の閉路を返す。"""
    if not tail_pts:
        return body
    x0, y0, x1, y1 = bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    base0, base1, tip = tail_pts
    n = len(body)

    def angle(p):
        return math.atan2((p[1] - cy) / ry, (p[0] - cx) / rx) % (2 * math.pi)

    def nearest(a):
        best_i, best_d = 0, 1e18
        for i in range(n):
            t = 2 * math.pi * i / n
            d = abs((t - a + math.pi) % (2 * math.pi) - math.pi)
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    ia, ib = nearest(angle(base0)), nearest(angle(base1))
    if ia == ib:
        ib = (ib + 1) % n
    fwd = (ib - ia) % n  # ia から ib への前方ステップ数
    if fwd <= n - fwd:
        # 前方(ia→ib)が短い＝しっぽ口。逆回り(ib→…→ia)の長い弧を残す
        keep = [body[(ib + k) % n] for k in range((ia - ib) % n + 1)]
    else:
        keep = [body[(ia + k) % n] for k in range(fwd + 1)]
    return keep + [tip]


# ---------------------------------------------------------------------------
# 文節解析にもとづく自動改行
# ---------------------------------------------------------------------------
# 文節の区切りで、これらの直後は「句読点での自然な改行位置」とみなす
BREAK_PUNCT = "。、，．！？!?…―"

_TOKENIZER = None


def get_tokenizer():
    global _TOKENIZER
    if _TOKENIZER is None:
        from janome.tokenizer import Tokenizer
        _TOKENIZER = Tokenizer()
    return _TOKENIZER


def bunsetsu_split(text: str) -> list[str]:
    """janome の形態素解析をもとに、文を文節（自立語＋付属語）へ分割する。

    付属語（助詞・助動詞・接尾・非自立）は前の語にくっつけ、自立語の手前で区切る。
    句読点・感嘆符などの直後では必ず区切る。
    """
    if not text:
        return []
    units: list[str] = []
    cur = ""
    prev_prefix = False  # 直前が接頭詞なら、続く自立語はくっつける（お＋菓子 など）
    for tok in get_tokenizer().tokenize(text):
        pos = tok.part_of_speech.split(",")
        major, sub = pos[0], (pos[1] if len(pos) > 1 else "")
        s = tok.surface
        attach = (
            major in ("助詞", "助動詞")
            or sub in ("接尾", "非自立")
            or prev_prefix
        )
        if major == "記号":
            cur += s
            if sub in ("句点", "読点") or s in BREAK_PUNCT:
                units.append(cur)
                cur = ""
        elif attach or not cur:
            cur += s
        else:
            units.append(cur)
            cur = s
        prev_prefix = major == "接頭詞"
    if cur:
        units.append(cur)
    return units


def pack_units(para_units: list[list[str]], max_len: int) -> tuple[list[str], int]:
    """文節リストを 1 行(列) max_len 文字以内に貪欲に詰める。文節は分割しない。

    戻り値は (行リスト, 句読点以外で改行した回数)。後者はペナルティ計算に使う。
    段落境界（明示改行）はユーザー指定なのでペナルティに数えない。
    """
    lines: list[str] = []
    unnatural = 0
    for units in para_units:
        if not units:
            lines.append("")
            continue
        cur = ""
        for u in units:
            if cur and len(cur) + len(u) > max_len:
                lines.append(cur)
                if cur[-1] not in BREAK_PUNCT:   # 句読点以外で切った＝やや不自然
                    unnatural += 1
                cur = u
            else:
                cur += u
        if cur:
            lines.append(cur)
    return (lines or [""]), unnatural


def text_block_size(layout: list[str], args, font) -> tuple[float, float]:
    """行(横書き) または 列(縦書き) のリストから、文字ブロックの幅・高さを返す。"""
    if args.vertical:
        size = args.font_size
        text_w = size * args.col_gap * len(layout)
        text_h = size * args.char_gap * max((len(c) for c in layout), default=1)
    else:
        ascent, descent = font.getmetrics()
        line_h = (ascent + descent) * args.line_gap
        text_h = line_h * len(layout)
        tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        text_w = max((tmp.textlength(l, font=font) for l in layout), default=1)
    return text_w, text_h


def bubble_box(text_w: float, text_h: float, args) -> tuple[float, float]:
    """文字ブロックから吹き出し本体の幅・高さを求める。"""
    pad = args.padding
    if args.shape in ("ellipse", "jagged", "burst", "hand"):
        bw, bh = text_w * 1.5 + pad * 2, text_h * 1.5 + pad * 2
    else:
        bw, bh = text_w + pad * 2, text_h + pad * 2
    return max(bw, args.font_size * 2), max(bh, args.font_size * 2)


def tail_unit_dir(args):
    """しっぽの外向き単位ベクトル（bounds 不要版）。しっぽ無しなら None。"""
    if args.tail_clock is not None:
        th = clock_to_theta(args.tail_clock)
        return (math.sin(th), -math.cos(th))
    if args.tail == "none":
        return None
    dx, dy = NAMED_TAIL_DIR[args.tail]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n)


def predicted_canvas(text_w: float, text_h: float, args) -> tuple[float, float]:
    """トリミング後の画像サイズの近似（縦横比の見積もり用）。"""
    bw, bh = bubble_box(text_w, text_h, args)
    d = tail_unit_dir(args)
    if d is not None:
        tl = min(bw, bh) * 0.225 * args.tail_scale
        return bw + abs(d[0]) * tl, bh + abs(d[1]) * tl
    return bw, bh


def parse_aspect(s: str) -> float:
    """\"横:縦\"（例 \"3:5\" は幅3・高さ5の縦長）または数値を、目標の 高さ/幅 比に変換する。"""
    if ":" in s:
        w, h = s.split(":")
        return float(h) / float(w)
    return float(s)


def flatten_units(text: str):
    """文節へ分割し、段落境界（明示改行）を強制改行として平坦化する。

    戻り値: (units, forced_after, has_empty_para)
      forced_after[k] が True なら units[k] の直後で必ず改行する（段落区切り）。
    """
    paras = [bunsetsu_split(p) for p in text.split("\n")]
    has_empty = any(len(p) == 0 for p in paras)
    units: list[str] = []
    forced_after: list[bool] = []
    for pi, p in enumerate(paras):
        for u in p:
            units.append(u)
            forced_after.append(False)
        if pi < len(paras) - 1 and units:
            forced_after[-1] = True  # 段落の最後の文節の後で強制改行
    return units, forced_after, has_empty


def dp_line_break(units, forced_after, break_penalty: float):
    """全分割を考慮する DP。各行数 n について最適な分割（句読点以外の改行を最小→行長を均等）を求める。

    コスト = Σ(行長^2)  ＋  (句読点以外で改行した回数) × BIG
    BIG を行長^2 の総和より十分大きく取ることで、まず不自然な改行を最小化し、
    その範囲で行長のばらつき（二乗和）を最小化する。break_penalty=0 なら均等化のみ。
    """
    m = len(units)
    length = [len(u) for u in units]
    pre = [0] * (m + 1)
    for i in range(m):
        pre[i + 1] = pre[i] + length[i]
    big = 10 ** 7 if break_penalty > 0 else 0
    inf = float("inf")
    # dp[k][i] = (cost, 直前の区切り位置 j)。先頭 i 文節を k 行に分割。
    dp = [[(inf, -1)] * (m + 1) for _ in range(m + 1)]
    dp[0][0] = (0.0, -1)
    for k in range(1, m + 1):
        for i in range(1, m + 1):
            best = (inf, -1)
            # 最後の行 = units[j:i]。j を i-1 から左へ。途中に強制改行があれば打ち切り。
            for j in range(i - 1, -1, -1):
                if j < i - 1 and forced_after[j]:
                    break
                base = dp[k - 1][j][0]
                if base < inf:
                    seglen = pre[i] - pre[j]
                    cost = base + seglen * seglen
                    if i < m and units[i - 1][-1] not in BREAK_PUNCT:
                        cost += big  # この行の後ろは（最終行でなく）句読点以外での改行
                    if cost < best[0]:
                        best = (cost, j)
            dp[k][i] = best
    return dp


def dp_reconstruct(dp, units, n: int) -> list[str]:
    m = len(units)
    segs = []
    i, k = m, n
    while k > 0:
        j = dp[k][i][1]
        segs.append("".join(units[j:i]))
        i, k = j, k - 1
    segs.reverse()
    return segs


def count_unnatural(layout: list[str]) -> int:
    """最終行を除く各行末が句読点でない＝不自然な改行の回数。"""
    return sum(1 for ln in layout[:-1] if ln and ln[-1] not in BREAK_PUNCT)


def auto_layout(text: str, args, font) -> list[str]:
    """文節で区切り、縦横比が --aspect に近く、かつ句読点以外での改行が少ない折り返しを選ぶ。"""
    target = parse_aspect(args.aspect)  # 縦/横
    units, forced_after, has_empty = flatten_units(text)
    if not units:
        return [""]
    if has_empty:  # 空段落を含むときは従来の貪欲法にフォールバック
        return _greedy_auto(text, args, font, target)
    m = len(units)
    dp = dp_line_break(units, forced_after, args.break_penalty)
    inf = float("inf")
    best = None
    for n in range(1, m + 1):
        if dp[n][m][0] == inf:
            continue
        layout = dp_reconstruct(dp, units, n)
        unnatural = count_unnatural(layout)
        tw, th = text_block_size(layout, args, font)
        cw, ch = predicted_canvas(tw, th, args)
        score = abs(ch / cw - target) + args.break_penalty * unnatural
        if best is None or score < best[0]:
            best = (score, layout)
    return best[1]


def layout_n_lines(text: str, n: int, break_penalty: float = 0.0) -> list[str]:
    """文節で区切り、指定行数(縦書きでは列数) n に収め、句読点以外での改行を避ける。"""
    units, forced_after, has_empty = flatten_units(text)
    if not units:
        return [""]
    if has_empty:
        para_units = [bunsetsu_split(p) for p in text.split("\n")]
        return _greedy_n_lines(para_units, n, break_penalty)
    m = len(units)
    dp = dp_line_break(units, forced_after, break_penalty)
    inf = float("inf")
    feasible = [k for k in range(1, m + 1) if dp[k][m][0] < inf]
    # n 行ちょうどが作れなければ、最も近い行数を選ぶ
    target_n = min(feasible, key=lambda k: abs(k - n))
    return dp_reconstruct(dp, units, target_n)


def _greedy_n_lines(para_units, n: int, break_penalty: float) -> list[str]:
    """空段落を含む等の特殊ケース用の、貪欲な行数合わせ（フォールバック）。"""
    total = sum(len(u) for units in para_units for u in units)
    if total == 0:
        return [""]
    candidates = []
    for max_len in range(1, total + 1):
        layout, unnatural = pack_units(para_units, max_len)
        candidates.append((layout, unnatural, max((len(x) for x in layout), default=0)))
    exact = [c for c in candidates if len(c[0]) == n]
    if exact:
        key = (lambda c: (c[1], c[2])) if break_penalty > 0 else (lambda c: c[2])
        return min(exact, key=key)[0]
    return min(candidates, key=lambda c: (abs(len(c[0]) - n), c[1]))[0]


def _greedy_auto(text, args, font, target) -> list[str]:
    """空段落を含む等の特殊ケース用の、貪欲なアスペクト探索（フォールバック）。"""
    para_units = [bunsetsu_split(p) for p in text.split("\n")]
    total = sum(len(u) for units in para_units for u in units)
    best = None
    for max_len in range(1, total + 1):
        layout, unnatural = pack_units(para_units, max_len)
        tw, th = text_block_size(layout, args, font)
        cw, ch = predicted_canvas(tw, th, args)
        score = abs(ch / cw - target) + args.break_penalty * unnatural
        if best is None or score < best[0]:
            best = (score, layout)
    return best[1] if best else [""]


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def build_image(args) -> Image.Image:
    font = load_font(args.font_size, args.font, args.font_index)
    margin = max(args.line_width * 3, int(args.font_size * 0.6)) + args.padding

    # 行(列)の決定：行数指定 > 自動改行 > 手動の max-chars 折り返し
    if args.lines is not None:
        layout = layout_n_lines(args.text, args.lines, args.break_penalty)
    elif args.auto_wrap:
        layout = auto_layout(args.text, args, font)
    elif args.vertical:
        layout = wrap_vertical(args.text, args.max_chars)
    else:
        layout = wrap_horizontal(args.text, args.max_chars)

    text_w, text_h = text_block_size(layout, args, font)
    bubble_w, bubble_h = bubble_box(text_w, text_h, args)

    # しっぽの向き（時計指定 or 名前付き）を先に解決
    has_tail = args.tail_clock is not None or args.tail != "none"
    # しっぽぶんの余白
    tail_room = int(min(bubble_w, bubble_h) * 0.55) if has_tail else 0
    canvas_w = int(bubble_w + margin * 2 + tail_room)
    canvas_h = int(bubble_h + margin * 2 + tail_room)

    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bx0 = (canvas_w - bubble_w) / 2
    by0 = (canvas_h - bubble_h) / 2
    # しっぽの向きと反対側へ本体を少し寄せて、しっぽぶんの余白を作る
    _, tail_dir = resolve_tail(args, (bx0, by0, bx0 + bubble_w, by0 + bubble_h))
    if tail_dir is not None:
        bx0 -= tail_dir[0] * tail_room / 2
        by0 -= tail_dir[1] * tail_room / 2
    bounds = (bx0, by0, bx0 + bubble_w, by0 + bubble_h)

    fill = (255, 255, 255, 255)
    outline = (0, 0, 0, 255)
    tail_pts, _ = resolve_tail(args, bounds)
    draw_bubble(draw, bounds, args.shape, fill, outline, args.line_width, tail_pts,
                {"seed": args.seed, "wobble": args.wobble, "strokes": args.strokes})

    # 合成ボールド（文字の輪郭を太らせる）の太さ
    if args.bold_width is not None:
        stroke = args.bold_width
    elif args.bold:
        stroke = max(1, round(args.font_size * 0.045))
    else:
        stroke = 0

    cx = (bounds[0] + bounds[2]) / 2
    cy = (bounds[1] + bounds[3]) / 2
    if args.vertical:
        draw_vertical(draw, layout, font, int(cx), int(cy), args.char_gap, args.col_gap,
                      outline, stroke)
    else:
        draw_horizontal(draw, layout, font, int(cx), int(cy), args.line_gap, outline, stroke)

    if args.trim:
        bbox = img.getbbox()
        if bbox:
            b = args.padding // 2
            bbox = (max(0, bbox[0] - b), max(0, bbox[1] - b),
                    min(canvas_w, bbox[2] + b), min(canvas_h, bbox[3] + b))
            img = img.crop(bbox)
    return img


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="日本語テキストを漫画風の吹き出し画像（背景透過 PNG）にする。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("text", help="吹き出しに入れる日本語テキスト（\\n で改行）")
    p.add_argument("-o", "--output", default="bubble.png", help="出力ファイルパス")
    p.add_argument("--shape", default="hand",
                   choices=["ellipse", "rounded", "rectangle", "jagged", "burst", "hand"],
                   help="吹き出しの形（デフォルト=hand=手書き風）")
    p.add_argument("--tail", default="bottom",
                   choices=["bottom", "bottom-left", "bottom-right",
                            "top", "top-left", "top-right", "left", "right", "none"],
                   help="しっぽの向き")
    p.add_argument("--tail-clock", type=float, default=None,
                   help="しっぽの位置を時計の時間で指定（12=上, 3=右, 6=下, 9=左。例: 4.5）。--tail より優先")
    p.add_argument("--tail-scale", type=float, default=1.0, help="しっぽの大きさ倍率")
    p.add_argument("--vertical", action=argparse.BooleanOptionalAction, default=True,
                   help="縦書きにする（デフォルト）。横書きにするには --no-vertical または --horizontal")
    p.add_argument("--horizontal", dest="vertical", action="store_false",
                   help="横書きにする（--no-vertical と同じ）")
    p.add_argument("--font", default=None,
                   help="使用するフォントファイル(.ttf/.otf/.ttc)のパス。未指定ならシステムの日本語フォントを自動検出")
    p.add_argument("--font-index", type=int, default=0,
                   help="フォントコレクション(.ttc)内のフォント番号")
    p.add_argument("--font-size", type=int, default=48, help="フォントサイズ(px)")
    p.add_argument("--bold", action="store_true",
                   help="太字にする（文字の輪郭を太らせる合成ボールド。どのフォントでも有効）")
    p.add_argument("--bold-width", type=int, default=None,
                   help="太字の太さ(px)を直接指定（指定すると --bold より優先）")
    p.add_argument("--max-chars", type=int, default=8,
                   help="1 行(列)の最大文字数（--no-auto-wrap の手動折り返し時のみ）")
    p.add_argument("--auto-wrap", action=argparse.BooleanOptionalAction, default=True,
                   help="文節・句読点で区切って自動改行し、画像の縦横比を --aspect に近づける（デフォルト）。"
                        "手動の --max-chars 折り返しに戻すには --no-auto-wrap")
    p.add_argument("--aspect", default="3:5",
                   help="自動改行時の目標縦横比（横:縦、例 3:5 は幅3・高さ5の縦長）。--auto-wrap と併用")
    p.add_argument("--lines", type=int, default=None,
                   help="行数（縦書きでは列数）を指定して文節で自動改行。指定すると --aspect より優先")
    p.add_argument("--break-penalty", type=float, default=0.15,
                   help="句読点以外での改行1回あたりのペナルティ（自動改行の探索に加算）。0で無効")
    p.add_argument("--line-width", type=int, default=4, help="輪郭線の太さ(px)")
    p.add_argument("--padding", type=int, default=28, help="文字と縁の余白(px)")
    p.add_argument("--line-gap", type=float, default=1.1, help="横書きの行間倍率")
    p.add_argument("--char-gap", type=float, default=1.15, help="縦書きの字間倍率")
    p.add_argument("--col-gap", type=float, default=1.4, help="縦書きの列間倍率")
    # 手書き風(--shape hand)用
    p.add_argument("--seed", type=int, default=None,
                   help="手書き風の乱数シード（揺れ方が変わる）。未指定なら実行ごとにランダム")
    p.add_argument("--wobble", type=float, default=1.0, help="手書き風の輪郭の揺れ量")
    p.add_argument("--strokes", type=int, default=2, help="手書き風の線の重ね描き回数")
    p.add_argument("--no-trim", dest="trim", action="store_false",
                   help="余白の自動トリミングをしない")
    p.set_defaults(trim=True)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    # コマンドラインで渡された "\n"（2文字）を実際の改行に変換
    args.text = args.text.replace("\\n", "\n")
    # --seed 未指定なら実行ごとにランダム（後で再現できるよう値を表示）
    random_seed = args.seed is None
    if random_seed:
        args.seed = random.randrange(1_000_000)
    img = build_image(args)
    out = Path(args.output)
    img.save(out)
    msg = f"保存しました: {out}  ({img.width}x{img.height}, RGBA)"
    if args.shape == "hand":
        note = "ランダム" if random_seed else "指定"
        msg += f"  [seed={args.seed} ({note})]"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
