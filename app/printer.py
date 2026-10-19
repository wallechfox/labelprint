"""打印逻辑：调用脚本把 ESC/POS 指令写入打印机，并记录日志。

- 纯文本：scripts/print_label.sh
- 纯图片：scripts/print_image.sh
- 文字 + 图片混排：scripts/print_job.sh（服务端拼好整段字节流，一次写设备、最后切一次纸）
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

# ---------------- 配置（全部来自环境变量，由 config.env 注入） ----------------
DEVICE: str = os.getenv("LABEL_PRINT_DEVICE", "/dev/usb/lp0")
SCRIPT: Path = Path(os.getenv("LABEL_PRINT_SCRIPT", "/app/scripts/print_label.sh"))
IMAGE_SCRIPT: Path = Path(os.getenv("LABEL_PRINT_IMAGE_SCRIPT", "/app/scripts/print_image.sh"))
JOB_SCRIPT: Path = Path(os.getenv("LABEL_PRINT_JOB_SCRIPT", "/app/scripts/print_job.sh"))

ENCODING: str = os.getenv("LABEL_PRINT_ENCODING", "gbk")
# 对齐：0=左 1=居中 2=右
ALIGN: str = os.getenv("LABEL_PRINT_ALIGN", "0")
DEFAULT_ALIGN = ALIGN
# 字号倍率：1~4
SIZE: str = os.getenv("LABEL_PRINT_SIZE", "2")
DEFAULT_SIZE = SIZE
# 末尾空白行数（方便手撕）
FEED: str = os.getenv("LABEL_PRINT_FEED", "4")
DEFAULT_FEED = FEED
# 文字段与图片段之间的空行
GAP: str = os.getenv("LABEL_PRINT_GAP", "1")

# 图片：58mm 纸=384 点，80mm 纸=576 点
WIDTH_DOTS: int = int(os.getenv("LABEL_PRINT_WIDTH", "384"))
# 字符度量（点）：实测 58mm 纸 1x 一行可打 32 个半角数字 => 384/32 = 12
CHAR_W: int = int(os.getenv("LABEL_PRINT_CHAR_W", "12"))
CHAR_H: int = int(os.getenv("LABEL_PRINT_CHAR_H", "24"))
# 额外行距（点）与左右物理边距（点，两侧合计）
LINE_SPACING: int = int(os.getenv("LABEL_PRINT_LINE_SPACING", "0"))
MARGIN: int = int(os.getenv("LABEL_PRINT_MARGIN", "0"))
DITHER: bool = os.getenv("LABEL_PRINT_DITHER", "false").lower() in ("1", "true", "yes")
THRESHOLD: int = int(os.getenv("LABEL_PRINT_THRESHOLD", "150"))
MAX_IMAGE_MB: int = int(os.getenv("LABEL_PRINT_MAX_IMAGE_MB", "8"))
MAX_HEIGHT: int = int(os.getenv("LABEL_PRINT_MAX_HEIGHT", "1500"))
TIMEOUT: int = int(os.getenv("LABEL_PRINT_TIMEOUT", "60"))
LOG_FILE: Path = Path(os.getenv("LABEL_PRINT_LOG", "/var/log/label-print.log"))
LOG_LIMIT: int = int(os.getenv("LABEL_PRINT_LOG_LIMIT", "50"))

# 时间戳：默认开关 / 位置 / 格式 / 字号
TS_ENABLE: bool = os.getenv("LABEL_PRINT_TS_ENABLE", "false").lower() in ("1", "true", "yes")
TS_POS: str = os.getenv("LABEL_PRINT_TS_POS", "bottom")
TS_FORMAT: str = os.getenv("LABEL_PRINT_TS_FORMAT", "datetime")
TS_SIZE: str = os.getenv("LABEL_PRINT_TS_SIZE", "1")

# 可打印区域宽度（扣除左右边距）
PRINT_AREA: int = max(24, WIDTH_DOTS - MARGIN)

_lock = threading.Lock()

# 禁则：不允许出现在行首 / 行尾的字符
NO_LINE_START = set("，。、；：？！）］｝」』】》〉”’%·…—～,.;:?!)]}>")
NO_LINE_END = set("（［｛「『【《〈“‘([{<")

TIME_FORMATS: dict[str, str] = {
    "datetime": "%Y-%m-%d %H:%M:%S",       # 2026-10-17 13:45:02
    "datetime_min": "%Y-%m-%d %H:%M",      # 2026-10-17 13:45
    "cn_full": "%Y年%m月%d日 %H:%M:%S",     # 2026年10月17日 13:45:02
    "cn_short": "%m月%d日 %H:%M",           # 10月17日 13:45
    "date": "%Y-%m-%d",                    # 2026-10-17
    "time": "%H:%M",                       # 13:45
    "compact": "%Y%m%d%H%M%S",             # 20261017134502
}


# ---------------- 日志 ----------------
def _ensure_log_dir() -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def write_log(text: str, result: str, detail: str = "", source: str = "api") -> dict:
    record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "text": text,
        "result": result,
        "detail": detail,
    }
    with _lock:
        try:
            _ensure_log_dir()
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[warn] 写日志失败: {e}")
    return record


def _all_records() -> list[dict]:
    """读全部日志，返回最新在前的列表。"""
    if not LOG_FILE.exists():
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return [{"time": "", "text": "", "result": "error", "detail": f"读取日志失败: {e}"}]

    records: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            records.append({"time": "", "text": line, "result": "unknown", "detail": ""})
    return records


def read_logs(limit: int = LOG_LIMIT) -> list[dict]:
    """最近 limit 条（最新在前）。"""
    return _all_records()[: max(0, int(limit))]


def read_logs_page(page: int = 1, page_size: int = 10) -> tuple[list[dict], int]:
    """按页读取打印记录，返回 (本页记录, 总条数)。"""
    records = _all_records()
    total = len(records)
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    start = (page - 1) * page_size
    return records[start: start + page_size], total


def clear_logs() -> int:
    """清空打印记录文件，返回清掉的条数（失败返回 -1）。"""
    with _lock:
        if not LOG_FILE.exists():
            return 0
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                count = sum(1 for line in f if line.strip())
            with open(LOG_FILE, "w", encoding="utf-8"):
                pass
            return count
        except Exception as e:
            print(f"[warn] 清空日志失败: {e}")
            return -1


# ---------------- 通用：执行脚本 ----------------
def _build_env(align: str | None = None, size: str | None = None) -> dict:
    env = os.environ.copy()
    env["LP_DEVICE"] = DEVICE
    env["LP_ENCODING"] = ENCODING
    env["LP_ALIGN"] = str(align if align is not None else ALIGN)
    env["LP_SIZE"] = str(size if size is not None else SIZE)
    env["LP_FEED"] = FEED
    return env


def _run(cmd: list[str], env: dict, log_text: str, source: str, no_log: bool = False) -> tuple[bool, str]:
    # 打印前先检测打印机是否在线（挂载 /dev 后，符号链接不存在即代表设备离线），
    # 离线直接返回友好提示，不再让脚本抛出一串英文错误
    if not os.path.exists(DEVICE):
        msg = "打印机离线，请检查打印机电源是否打开"
        if not no_log:
            write_log(log_text, "error", msg, source)
        return False, msg
    try:
        # 按字节收输出再自行解码：Windows 本地按 GBK 解码 UTF-8 输出会直接抛异常
        proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        if not no_log:
            write_log(log_text, "error", f"打印超时（>{TIMEOUT}s）", source)
        return False, f"打印超时（>{TIMEOUT}s），请检查打印机是否离线或缺纸"
    except FileNotFoundError:
        if not no_log:
            write_log(log_text, "error", "未找到 bash（Windows 本地只能用 run_local.ps1 干跑）", source)
        return False, "未找到 bash：本地只能预览，真实打印要在 NAS 容器里执行"
    except Exception as e:
        if not no_log:
            write_log(log_text, "error", f"执行打印脚本异常: {e}", source)
        return False, f"执行打印脚本异常: {e}"

    stdout = (proc.stdout or b"").decode("utf-8", "replace").strip()
    stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()

    if proc.returncode == 0:
        if not no_log:
            write_log(log_text, "ok", stdout, source)
        return True, "打印成功"

    detail = stderr or stdout or f"退出码 {proc.returncode}"
    if not no_log:
        write_log(log_text, "error", detail, source)
    return False, detail


# ---------------- ESC/POS 片段构造 ----------------
def _size_byte(size: int) -> int:
    """字号倍率 1~8 -> GS ! n（低 3 位宽、4~6 位高）。"""
    v = max(0, min(7, int(size) - 1))
    return v | (v << 4)


def _encode(s: str, encoding: str | None = None) -> bytes:
    """文本转目标编码（GBK 等），编码名非法时回退 UTF-8。"""
    enc = encoding or ENCODING
    try:
        return s.encode(enc, errors="replace")
    except LookupError:
        return s.encode("utf-8", errors="replace")


# ---------------- 排版引擎（预览与打印共用，保证所见即所得） ----------------
def ts_text(fmt_key: str | None = None, now: datetime | None = None) -> str:
    """按预设格式生成当前时间字符串。"""
    now = now or datetime.now()
    key = fmt_key or TS_FORMAT
    return now.strftime(TIME_FORMATS.get(key, TIME_FORMATS["datetime"]))


def _units(ch: str) -> int:
    """半角 = 1 单位，全角 = 2 单位。"""
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def line_units(s: str) -> int:
    return sum(_units(c) for c in s)


def _tokens(s: str) -> list[str]:
    """切分：连续半角字母数字为一个整体（英文单词），全角逐字，空格单独成 token。"""
    toks: list[str] = []
    buf = ""
    for ch in s:
        if ch == " ":
            if buf:
                toks.append(buf)
                buf = ""
            toks.append(" ")
        elif _units(ch) == 2:
            if buf:
                toks.append(buf)
                buf = ""
            toks.append(ch)
        else:
            buf += ch
    if buf:
        toks.append(buf)
    return toks


def wrap_text(text: str, size: int = 2) -> tuple[list[dict], bool]:
    """按纸宽自动折行。

    返回 ([{"text": str, "hard": bool}], 是否发生过自动折行)

    - `hard=True` 表示这一行是用户手动回车产生的段落结尾（真换行）
    - `hard=False` 表示这一行是超宽自动折出来的（软换行），
      读回文本时不能当成 \\n，否则改小字号后不会重新折行
    - 英文按单词断，超长串逐字符硬断
    - 标点避头尾（禁则处理）
    """
    size = max(1, int(size))
    capacity = max(1, PRINT_AREA // (CHAR_W * size))
    rows: list[dict] = []
    wrapped = False

    for para in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        para_rows: list[str] = []
        if not para:
            para_rows.append("")
        else:
            line = ""
            units = 0
            for tok in _tokens(para):
                tu = line_units(tok)
                if tu > capacity:  # 超长不可断串：逐字符硬断
                    for ch in tok:
                        cu = _units(ch)
                        if units + cu > capacity and line:
                            para_rows.append(line)
                            wrapped = True
                            line = ""
                            units = 0
                        line += ch
                        units += cu
                    continue
                if units + tu > capacity and line:
                    # 行首禁则：标点不落行首，把上一行末字符一起挪下来
                    if tok and tok[0] in NO_LINE_START:
                        carry = line[-1]
                        para_rows.append(line[:-1])
                        wrapped = True
                        line = carry + tok
                        units = line_units(line)
                        continue
                    # 行尾禁则：开引号/开括号不落行尾
                    if line[-1] in NO_LINE_END:
                        carry = line[-1]
                        para_rows.append(line[:-1])
                        wrapped = True
                        line = carry + tok
                        units = line_units(line)
                        continue
                    para_rows.append(line)
                    wrapped = True
                    line = "" if tok == " " else tok
                    units = line_units(line)
                else:
                    line += tok
                    units += tu
            if line:
                para_rows.append(line)
        if not para_rows:
            para_rows.append("")
        for i, ln in enumerate(para_rows):
            rows.append({"text": ln, "hard": (i == len(para_rows) - 1)})
    return rows, wrapped


def image_metrics(image_bytes: bytes, rotate: int = 0) -> tuple[int, int]:
    """只算图片打印后的尺寸（不做二值化，供预览快速使用）。"""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    if int(rotate) in (90, 180, 270):
        img = img.rotate(-int(rotate), expand=True)
    w, h = img.size
    if w != WIDTH_DOTS:
        h = max(1, round(h * WIDTH_DOTS / w))
        w = WIDTH_DOTS
    if h > MAX_HEIGHT:
        w = max(1, round(w * MAX_HEIGHT / h))
        h = MAX_HEIGHT
    return w, h


def _norm_align(align: int | None) -> int:
    v = int(ALIGN) if align is None else int(align)
    return 0 if v < 0 else (2 if v > 2 else v)


def layout(
    text: str | None = None,
    image_bytes: bytes | None = None,
    align: int | None = None,
    size: int | None = None,
    order: str = "text-first",
    feed: int | None = None,
    rotate: int = 0,
    ts_enable: bool | None = None,
    ts_pos: str | None = None,
    ts_fmt: str | None = None,
    ts_size: int | None = None,
    now: datetime | None = None,
) -> dict:
    """排版：算出每个块（文字行 / 图片 / 空行 / 末尾空白）的几何位置。

    返回结构与打印字节流一一对应，前端照着画即可所见即所得。
    """
    align_v = _norm_align(align)
    size_v = int(SIZE) if size is None else max(1, int(size))
    feed_v = int(FEED) if feed is None else int(feed)
    feed_v = max(0, min(20, feed_v))
    gap_v = max(0, min(10, int(GAP)))
    ts_size_v = int(TS_SIZE) if ts_size is None else max(1, int(ts_size))
    ts_on = TS_ENABLE if ts_enable is None else bool(ts_enable)
    ts_pos_v = (ts_pos or TS_POS or "bottom").lower()
    if ts_pos_v not in ("top", "bottom"):
        ts_pos_v = "bottom"

    # ---- 内容块（按纵向顺序）----
    blocks: list[dict] = []
    wrapped = False
    line_count = 0

    ts_line = ts_text(ts_fmt, now) if ts_on else None

    text_rows: list[str] = []
    if text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # 不再裁掉首尾换行：末尾的空行就是用户在最后一行按的回车，
        # 裁掉会让「回车没反应」；空白行太多由用户自己删，末尾留白另有 feed 控制
        text_rows, wrapped = wrap_text(text, size_v)
        line_count = len(text_rows)

    img_wh: tuple[int, int] | None = None
    if image_bytes:
        img_wh = image_metrics(image_bytes, rotate)

    if ts_line and ts_pos_v == "top":
        blocks.append({"type": "text", "text": ts_line, "size": ts_size_v, "kind": "timestamp"})

    def _text_blocks() -> list[dict]:
        return [
            {"type": "text", "text": r["text"], "size": size_v, "kind": "text", "hard": bool(r["hard"])}
            for r in text_rows
        ]

    if text_rows and img_wh:
        seq_img = [{"type": "image", "w": img_wh[0], "h": img_wh[1]}]
        blocks += (seq_img + [{"type": "gap", "rows": gap_v}] + _text_blocks()) if order == "image-first" \
            else (_text_blocks() + [{"type": "gap", "rows": gap_v}] + seq_img)
    elif text_rows:
        blocks += _text_blocks()
    elif img_wh:
        blocks.append({"type": "image", "w": img_wh[0], "h": img_wh[1]})

    if ts_line and ts_pos_v == "bottom":
        blocks.append({"type": "text", "text": ts_line, "size": ts_size_v, "kind": "timestamp"})

    # ---- 计算几何 ----
    y = 0
    for b in blocks:
        b["y"] = y
        if b["type"] == "text":
            sz = b["size"]
            b["w"] = line_units(b["text"]) * CHAR_W * sz
            b["h"] = CHAR_H * sz + LINE_SPACING
            b["x"] = 0 if align_v == 0 else (
                max(0, (PRINT_AREA - b["w"]) // 2) if align_v == 1 else max(0, PRINT_AREA - b["w"])
            )
        elif b["type"] == "image":
            b["x"] = 0 if align_v == 0 else (
                max(0, (PRINT_AREA - b["w"]) // 2) if align_v == 1 else max(0, PRINT_AREA - b["w"])
            )
            b["h"] = b["h"]
        else:  # gap
            b["h"] = b["rows"] * (CHAR_H + LINE_SPACING)
            b["x"] = 0
            b["w"] = 0
        y += b["h"]

    feed_dots = feed_v * (CHAR_H + LINE_SPACING)
    blocks.append({"type": "feed", "y": y, "x": 0, "w": 0, "h": feed_dots, "rows": feed_v})

    return {
        "width_dots": PRINT_AREA,
        "blocks": blocks,
        "total_dots": y + feed_dots,
        "line_count": line_count,
        "wrapped": wrapped,
        "image": ({"w": img_wh[0], "h": img_wh[1]} if img_wh else None),
        "align": align_v,
        "size": size_v,
        "char_w": CHAR_W,
        "char_h": CHAR_H,
    }


def build_text_bytes(lines: list[str], align: int = 0, size: int = 2, encoding: str | None = None) -> bytes:
    """文字段：对齐 + 字号 + 每行文本（自动转 GBK 等目标编码）。"""
    enc = encoding or ENCODING
    out = bytearray()
    out += b"\x1b\x61" + bytes([max(0, min(2, int(align)))])
    out += b"\x1d\x21" + bytes([_size_byte(size)])
    for line in lines:
        try:
            out += line.encode(enc, errors="replace")
        except LookupError:
            out += line.encode("utf-8", errors="replace")
        out += b"\n"
    return bytes(out)


def build_image_bytes(
    image_bytes: bytes,
    align: int = 0,
    rotate: int = 0,
    dither: bool | None = None,
    invert: bool = False,
    threshold: int | None = None,
    brightness: float = 1.0,
    contrast: float = 1.0,
) -> tuple[bytes, int, int]:
    """图片段：对齐 + GS v 0 光栅位图，返回 (字节, 宽, 高)。"""
    from PIL import Image, ImageEnhance, ImageOps

    do_dither = DITHER if dither is None else bool(dither)
    thr = THRESHOLD if threshold is None else int(threshold)

    img = Image.open(io.BytesIO(image_bytes))

    # 透明背景补白
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg

    # 旋转（90/180/270，顺时针；expand=True 保证不裁切）
    if int(rotate) in (90, 180, 270):
        img = img.rotate(-int(rotate), expand=True)

    gray = img.convert("L")

    # 亮度 / 对比度
    try:
        if abs(float(brightness) - 1.0) > 1e-3:
            gray = ImageEnhance.Brightness(gray).enhance(float(brightness))
        if abs(float(contrast) - 1.0) > 1e-3:
            gray = ImageEnhance.Contrast(gray).enhance(float(contrast))
    except Exception:
        pass

    # 反色（白底黑字 -> 黑底白字）
    if invert:
        gray = ImageOps.invert(gray)

    # 等比缩放到打印宽度
    if gray.width != WIDTH_DOTS:
        h = max(1, round(gray.height * WIDTH_DOTS / gray.width))
        gray = gray.resize((WIDTH_DOTS, h), Image.LANCZOS)

    # 限制高度
    if gray.height > MAX_HEIGHT:
        w = max(1, round(gray.width * MAX_HEIGHT / gray.height))
        gray = gray.resize((w, MAX_HEIGHT), Image.LANCZOS)

    bw = gray.convert("1") if do_dither else gray.point(lambda p: 255 if p >= thr else 0).convert("1")

    w_bytes = (bw.width + 7) // 8
    height = bw.height
    out = bytearray()
    out += b"\x1b\x61" + bytes([max(0, min(2, int(align)))])
    out += b"\x1d\x76\x30\x00"
    out += bytes([w_bytes & 0xFF, (w_bytes >> 8) & 0xFF, height & 0xFF, (height >> 8) & 0xFF])

    pixels = bw.load()
    for y in range(height):
        row = bytearray(w_bytes)
        for x in range(bw.width):
            if pixels[x, y] == 0:  # 1 模式下 0 = 黑点
                row[x >> 3] |= 0x80 >> (x & 7)
        out += row

    return bytes(out), bw.width, height


# ---------------- 文本打印（原接口，保持兼容） ----------------
def print_lines(
    lines: list[str],
    source: str = "api",
    align: int | None = None,
    size: int | None = None,
) -> tuple[bool, str]:
    lines = [str(line) for line in lines]
    if not any(line.strip() for line in lines):
        return False, "打印内容为空"
    if not SCRIPT.exists():
        write_log("\n".join(lines), "error", f"打印脚本不存在: {SCRIPT}", source)
        return False, f"打印脚本不存在: {SCRIPT}"

    cmd = ["bash", str(SCRIPT), *lines]
    return _run(cmd, _build_env(align, size), "\n".join(lines), source)


def print_text(
    text: str,
    source: str = "api",
    align: int | None = None,
    size: int | None = None,
) -> tuple[bool, str]:
    """打印文字。统一走排版引擎（自动折行），保证与预览一致。"""
    if not (text or "").strip():
        return False, "打印内容为空"
    return print_job(text=text, source=source, align=align, size=size)


# ---------------- 图片打印（原接口，保持兼容） ----------------
def build_raster(
    image_bytes: bytes,
    dither: bool | None = None,
    rotate: int = 0,
    invert: bool = False,
    threshold: int | None = None,
    brightness: float = 1.0,
    contrast: float = 1.0,
) -> tuple[bytes, int, int]:
    seg, w, h = build_image_bytes(
        image_bytes,
        dither=dither,
        rotate=rotate,
        invert=invert,
        threshold=threshold,
        brightness=brightness,
        contrast=contrast,
    )
    return seg[2:], w, h  # 去掉对齐指令（由调用方统一发 ESC a）


def print_image(
    image_bytes: bytes,
    source: str = "web",
    align: int | None = None,
    dither: bool | None = None,
) -> tuple[bool, str]:
    if not IMAGE_SCRIPT.exists():
        write_log("[图片]", "error", f"图片打印脚本不存在: {IMAGE_SCRIPT}", source)
        return False, f"图片打印脚本不存在: {IMAGE_SCRIPT}"

    try:
        raster, w, h = build_raster(image_bytes, dither=dither)
    except Exception as e:
        write_log("[图片]", "error", f"图片处理失败: {e}", source)
        return False, f"图片处理失败: {e}"

    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".bin")
        with os.fdopen(fd, "wb") as f:
            f.write(raster)
        cmd = ["bash", str(IMAGE_SCRIPT), tmp_path]
        return _run(cmd, _build_env(align=align), f"[图片 {w}x{h}]", source)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ---------------- 混排任务（文字 + 图片，一次出纸一次切） ----------------
def build_job_bytes(
    text: str | None = None,
    image_bytes: bytes | None = None,
    align: int | None = None,
    size: int | None = None,
    order: str = "text-first",
    feed: int | None = None,
    rotate: int = 0,
    dither: bool | None = None,
    invert: bool = False,
    threshold: int | None = None,
    brightness: float = 1.0,
    contrast: float = 1.0,
    ts_enable: bool | None = None,
    ts_pos: str | None = None,
    ts_fmt: str | None = None,
    ts_size: int | None = None,
    now: datetime | None = None,
) -> tuple[bytes, str]:
    """拼出整段 ESC/POS 字节流（含初始化、正文、图片、时间戳、末尾空白行）。

    排版完全由 layout() 决定，与 /preview 返回一致（所见即所得）。
    返回 (字节流, 日志摘要)。切纸指令由 print_job.sh 追加。
    """
    if not (text and text.strip()) and not image_bytes:
        raise ValueError("没有可打印的内容（文字和图片都为空）")

    lay = layout(
        text=text,
        image_bytes=image_bytes,
        align=align,
        size=size,
        order=order,
        feed=feed,
        rotate=rotate,
        ts_enable=ts_enable,
        ts_pos=ts_pos,
        ts_fmt=ts_fmt,
        ts_size=ts_size,
        now=now,
    )

    align_v = lay["align"]
    out = bytearray(b"\x1b\x40")                      # 初始化
    # 行距：不能发 ESC 3 0（行距 0 点）
    # 该打印机文字行按字符高度自动走纸，但空行只靠行距走纸，
    # 行距为 0 时空行的 \n 完全不走纸 → 末尾空白行失效、最后一行留在机头里。
    if LINE_SPACING > 0:
        out += b"\x1b\x33" + bytes([min(255, LINE_SPACING)])       # ESC 3 n 额外行距
    else:
        out += b"\x1b\x32"                                        # ESC 2 默认行距
    out += b"\x1b\x61" + bytes([align_v])             # 对齐
    info_parts: list[str] = []

    raster = b""
    if image_bytes:
        raster, iw, ih = build_raster(
            image_bytes,
            dither=dither,
            rotate=rotate,
            invert=invert,
            threshold=threshold,
            brightness=brightness,
            contrast=contrast,
        )
        info_parts.append(f"图片{iw}x{ih}")

    for b in lay["blocks"]:
        kind = b["type"]
        if kind == "text":
            out += b"\x1d\x21" + bytes([_size_byte(b["size"])])   # 每行独立字号
            out += _encode(b["text"])
            out += b"\n"
        elif kind == "image":
            out += b"\x1b\x61" + bytes([align_v])
            out += raster
        elif kind in ("gap", "feed"):
            rows = max(0, int(b.get("rows", 1)))
            if rows:
                out += b"\x1d\x21\x00"                 # 复位 1x，避免空行过高
                out += b"\n" * rows

    log_text = (text or "") + (" " + " ".join(info_parts) if info_parts else "")
    return bytes(out), (log_text.strip() or "[空任务]")


def print_job(
    text: str | None = None,
    image_bytes: bytes | None = None,
    source: str = "web",
    align: int | None = None,
    size: int | None = None,
    order: str = "text-first",
    feed: int | None = None,
    rotate: int = 0,
    dither: bool | None = None,
    invert: bool = False,
    threshold: int | None = None,
    brightness: float = 1.0,
    contrast: float = 1.0,
    ts_enable: bool | None = None,
    ts_pos: str | None = None,
    ts_fmt: str | None = None,
    ts_size: int | None = None,
    now: datetime | None = None,
    no_log: bool = False,
) -> tuple[bool, str]:
    """打印混排任务。no_log=True（无痕模式）时不写入打印记录。"""
    if not JOB_SCRIPT.exists():
        if not no_log:
            write_log(text or "[图片]", "error", f"任务打印脚本不存在: {JOB_SCRIPT}", source)
        return False, f"任务打印脚本不存在: {JOB_SCRIPT}"

    try:
        payload, log_text = build_job_bytes(
            text=text,
            image_bytes=image_bytes,
            align=align,
            size=size,
            order=order,
            feed=feed,
            rotate=rotate,
            dither=dither,
            invert=invert,
            threshold=threshold,
            brightness=brightness,
            contrast=contrast,
            ts_enable=ts_enable,
            ts_pos=ts_pos,
            ts_fmt=ts_fmt,
            ts_size=ts_size,
            now=now,
        )
    except ValueError as e:
        return False, str(e)
    except Exception as e:
        if not no_log:
            write_log(text or "[图片]", "error", f"图片处理失败: {e}", source)
        return False, f"图片处理失败: {e}"

    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".bin")
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        cmd = ["bash", str(JOB_SCRIPT), tmp_path]
        return _run(
            cmd,
            _build_env(align=align, size=size),
            log_text,
            source,
            no_log=no_log,
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
