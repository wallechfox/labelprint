#!/usr/bin/env bash
# ============================================================
# print_label.sh —— ESC/POS 标签打印脚本（爱宝 A5870）
#
# 用法：
#   ./print_label.sh "李明" "三年级2班" "请假条"
#   printf '李明\n三年级2班\n' | ./print_label.sh     # 无参数时从 stdin 读
#
# 可用环境变量：
#   LP_DEVICE   打印机设备，默认 /dev/usb/lp0
#   LP_ENCODING 输出编码，默认 gbk（爱宝 A5870 中文码页），可设 utf-8/gb18030/none
#   LP_ALIGN    对齐：0=左 1=居中 2=右
#   LP_SIZE     字号倍率：1=正常 2=2倍 3=3倍 4=4倍
#   LP_FEED     打印后额外走纸行数，默认 2
# ============================================================
set -uo pipefail

DEV="${LP_DEVICE:-/dev/usb/lp0}"
ENC="${LP_ENCODING:-gbk}"
FEED="${LP_FEED:-2}"
ALIGN="${LP_ALIGN:-0}"
SIZE="${LP_SIZE:-2}"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# 写入一个转义序列（%b 会把 \xNN 解析为字节）
esc() { printf "%b" "$1" >>"$TMP"; }

# ---------- 1. 初始化打印机 ESC @ ----------
esc '\x1b\x40'
# ---------- 2. 对齐方式 ESC a n（0=左 1=居中 2=右） ----------
esc "\\x1b\\x61\\x0${ALIGN}"
# ---------- 3. 字号 GS ! n（宽高同步放大） ----------
case "${SIZE}" in
1) esc '\x1d\x21\x00' ;;   # 正常
2) esc '\x1d\x21\x11' ;;   # 宽高 2 倍
3) esc '\x1d\x21\x22' ;;   # 宽高 3 倍
4) esc '\x1d\x21\x33' ;;   # 宽高 4 倍
*) esc '\x1d\x21\x11' ;;
esac

# ---------- 4. 正文：每行末尾加 \x0a ----------
emit_line() {
    local line="$1"
    case "$ENC" in
    none | utf-8 | utf8 | UTF-8)
        printf '%s' "$line" >>"$TMP"
        ;;
    *)
        # 输入为 UTF-8，按目标编码转码；iconv 缺失或失败时原样输出
        if command -v iconv >/dev/null 2>&1; then
            printf '%s' "$line" | iconv -f UTF-8 -t "$ENC" >>"$TMP" 2>/dev/null ||
                printf '%s' "$line" >>"$TMP"
        else
            printf '%s' "$line" >>"$TMP"
        fi
        ;;
    esac
    printf '\x0a' >>"$TMP"
}

if [ "$#" -gt 0 ]; then
    for line in "$@"; do
        emit_line "$line"
    done
else
    # 无参数：从标准输入按行读取
    while IFS= read -r line || [ -n "$line" ]; do
        emit_line "$line"
    done
fi

# 额外走纸（默认 2 行，避免最后一行还留在切刀内侧）
if [ "$FEED" -gt 0 ] 2>/dev/null; then
    for ((i = 0; i < FEED; i++)); do
        printf '\x0a' >>"$TMP"
    done
fi

# ---------- 5. 走纸到切刀位置 GS V B 0 ----------
esc '\x1d\x56\x42\x00'
# ---------- 6. 切纸 GS V 0 ----------
esc '\x1d\x56\x00'

# ---------- 写设备 ----------
if [ ! -e "$DEV" ]; then
    echo "ERROR: 打印设备不存在或未挂载: $DEV" >&2
    exit 2
fi

if [ ! -w "$DEV" ]; then
    echo "ERROR: 打印设备不可写（权限不足）: $DEV" >&2
    exit 3
fi

if ! cat "$TMP" >"$DEV" 2>/tmp/lp_err; then
    echo "ERROR: 写入打印设备失败: $DEV ; $(cat /tmp/lp_err 2>/dev/null)" >&2
    exit 4
fi

echo "OK: 已写入 $DEV"
exit 0
