#!/bin/sh
# 本地 ComfyUI 的起 / 停 / 看日志。
#
# 工作台本身不管引擎生命周期（docs/adr/0004）；这个脚本存在的唯一理由是：
# 没有终端的 agent 也需要一份能照抄的启动方式 —— docs 里那句 conda activate
# 在无 tty 环境跑不了。
#
#   ./scripts/comfy.sh start    起引擎（已在跑就不重复起），等到 8188 就绪才返回
#   ./scripts/comfy.sh stop     停掉引擎进程
#   ./scripts/comfy.sh status   查 8188 通不通
#   ./scripts/comfy.sh log      看日志尾巴
#
# 换机器时用环境变量盖掉默认路径：COMFY_ROOT / COMFY_PYTHON。
set -eu

COMFY_ROOT="${COMFY_ROOT:-/home/xiao/workspace/comfyUI/ComfyUI}"
COMFY_PYTHON="${COMFY_PYTHON:-/home/xiao/miniconda3/envs/comfyui/bin/python}"
COMFY_LOG="${COMFY_LOG:-/tmp/comfyui.log}"
PORT="${COMFY_PORT:-8188}"

ready() {
    curl -fsS "http://127.0.0.1:$PORT/system_stats" >/dev/null 2>&1
}

if [ "$#" -eq 0 ]; then
    echo "用法：$0 start|stop|status|log" >&2
    exit 2
fi

action="$1"

case "$action" in
    start)
        if ready; then
            echo "已经在跑了：http://127.0.0.1:$PORT"
            exit 0
        fi
        cd "$COMFY_ROOT"
        setsid "$COMFY_PYTHON" main.py >"$COMFY_LOG" 2>&1 </dev/null &
        echo "起了引擎（日志 $COMFY_LOG），等它就绪……"
        i=0
        while [ "$i" -lt 90 ]; do
            if ready; then
                echo "就绪：http://127.0.0.1:$PORT"
                exit 0
            fi
            i=$((i + 1))
            sleep 1
        done
        echo "等了 90 秒还没就绪，看日志：$COMFY_LOG" >&2
        exit 1
        ;;
    stop)
        pids="$(pgrep -f "$COMFY_PYTHON main.py" || true)"
        if [ -z "$pids" ]; then
            echo "没有在跑。"
            exit 0
        fi
        kill $pids
        echo "已停：$pids"
        ;;
    status)
        if ready; then
            echo "通：http://127.0.0.1:$PORT"
        else
            echo "8188 连不上 —— 引擎没起，跑 ./scripts/comfy.sh start" >&2
            exit 1
        fi
        ;;
    log)
        tail -n 50 "$COMFY_LOG"
        ;;
    *)
        echo "用法：$0 start|stop|status|log" >&2
        exit 2
        ;;
esac