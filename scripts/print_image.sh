#!/usr/bin/env bash
# ============================================================
# print_image.sh —— 打印 ESC/POS 光栅位图（GS v 0）
#
# 用法：
#   ./print_image.sh /tmp/xxx.bin
# 其中 bin 文件由 app/printer.py 生成，内容就是 GS v 0 的完整指令+位图数据。
#
# 可用环境变量：
#   LP_DEVICE   打印机设备，默认 /dev/usb/lp0
#   LP_ALIGN    对齐：0=左 1=居中 2=右
#   LP_FEED     图后额外走纸行数，默认 2
# ============================================================
set -uo pipefail

DEV="${LP_DEVICE:-/dev/usb/lp0}"
ALIGN="${LP_ALIGN:-0}"
FEED="${LP_FEED:-2}"
BIN="$1"

if [ -z "${BIN:-}" ] || [ ! -f "$BIN" ]; then
    echo "ERROR: 位图文件不存在: ${BIN:-<空>}" >&2
    exit 5
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

esc() { printf "%b" "$1" >>"$TMP"; }

# 1. 初始化
esc '\x1b\x40'
# 2. 对齐
esc "\\x1b\\x61\\x0${ALIGN}"
# 3. 位图数据
cat "$BIN" >>"$TMP"
# 4. 图后走纸
if [ "$FEED" -gt 0 ] 2>/dev/null; then
    for ((i = 0; i < FEED; i++)); do
        printf '\x0a' >>"$TMP"
    done
fi
# 5. 走纸到切刀 + 切纸
esc '\x1d\x56\x42\x00'
esc '\x1d\x56\x00'

if [ ! -e "$DEV" ]; then
    echo "ERROR: 打印设备不存在或未挂载: $DEV" >&2
    exit 2
fi
if [ ! -w "$DEV" ]; then
    echo "ERROR: 打印设备不可写（权限不足）: $DEV" >&2
    exit 3
fi
if ! cat "$TMP" >"$DEV" 2>/tmp/lp_img_err; then
    echo "ERROR: 写入打印设备失败: $DEV ; $(cat /tmp/lp_img_err 2>/dev/null)" >&2
    exit 4
fi

echo "OK: 已写入 $DEV"
exit 0
