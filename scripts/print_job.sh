#!/usr/bin/env bash
# ============================================================
# print_job.sh —— 打印一段完整的 ESC/POS 字节流（文字+图片混排用）
#
# 用法：
#   ./print_job.sh /tmp/xxx.bin
# bin 文件由 app/printer.py 生成，内部已包含：初始化、对齐、字号、
# 文本、位图、末尾空白行。本脚本只负责写设备并走纸切纸。
#
# 可用环境变量：
#   LP_DEVICE   打印机设备，默认 /dev/usb/lp0
# ============================================================
set -uo pipefail

DEV="${LP_DEVICE:-/dev/usb/lp0}"
BIN="${1:-}"

if [ -z "$BIN" ] || [ ! -f "$BIN" ]; then
    echo "ERROR: 任务文件不存在: ${BIN:-<空>}" >&2
    exit 5
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

esc() { printf "%b" "$1" >>"$TMP"; }

# 1. 任务字节流（初始化/正文/图片/末尾空白都已包含）
cat "$BIN" >>"$TMP"
# 2. 走纸到切刀位置
esc '\x1d\x56\x42\x00'
# 3. 切纸
esc '\x1d\x56\x00'

if [ ! -e "$DEV" ]; then
    echo "ERROR: 打印设备不存在或未挂载: $DEV" >&2
    exit 2
fi
if [ ! -w "$DEV" ]; then
    echo "ERROR: 打印设备不可写（权限不足）: $DEV" >&2
    exit 3
fi
if ! cat "$TMP" >"$DEV" 2>/tmp/lp_job_err; then
    echo "ERROR: 写入打印设备失败: $DEV ; $(cat /tmp/lp_job_err 2>/dev/null)" >&2
    exit 4
fi

echo "OK: 已写入 $DEV"
exit 0
