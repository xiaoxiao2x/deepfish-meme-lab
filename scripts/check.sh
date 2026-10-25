#!/bin/sh
# 门禁：改完代码跑这个。五条都过才算过。
#
#   ./scripts/check.sh         全跑（pytest / ruff / mypy / docs / js）
#   ./scripts/check.sh ruff    只跑其中一项（pytest / ruff / mypy / docs / js）
#
# 提交前会由 scripts/git-hooks/pre-commit 自动调一次；那次确实想跳过：
#   SKIP_CHECKS=1 git commit ...
set -eu

cd "$(dirname "$0")/.."

# 优先用 .venv 里的工具（建法见 README「跑起来」），没有就退回 PATH。
pick() {
    if [ -x ".venv/bin/$1" ]; then echo ".venv/bin/$1"; else echo "$1"; fi
}

PY="$(pick python)"
PYTEST="$(pick pytest)"
RUFF="$(pick ruff)"
MYPY="$(pick mypy)"

failed=0

step() {
    name="$1"
    shift
    printf "\n== %s ==\n" "$name"
    if "$@"; then
        return 0
    fi
    failed=1
}

# mypy 对 [tool.mypy] 里不认识的键只打一行 Unrecognized option 就照常成功
# （mypy 2.4 没有 --strict-config）。配置写错的后果是「其实没在检查」，
# 所以这里自己把那一行当失败。
# 查哪些文件由 pyproject 的 [tool.mypy] files 说了算 —— 这里**不带**目录参数，
# 让「查什么」只有一个来源。带上来就会盖掉配置文件，两处各说各的（这条已经
# 错过一次：files 只写了 lab，tests 里的六个错误一直没被查）。
mypy_gate() {
    out="$("$MYPY" 2>&1)" && status=0 || status=$?
    printf "%s\n" "$out"
    if printf "%s" "$out" | grep -q "Unrecognized option"; then
        printf "mypy 报了 Unrecognized option：pyproject 的 [tool.mypy] 里有它不认识的键，先改对。\n" >&2
        return 1
    fi
    # 「errors prevented further checking」= 中途放弃了，剩下的文件一个都没查。
    # 退出码本来就非 0、门禁已经红了，但那条红和「查完有 N 个错误」在报告里长得一样，
    # 容易让人以为修完这几个就干净了。明说一句。
    if printf "%s" "$out" | grep -q "errors prevented further checking"; then
        printf "mypy 中途放弃了（上面那行 errors prevented further checking）—— 它之后要查的文件一个都没查，这次的报告不是全部结论。\n" >&2
    fi
    return "$status"
}

# 浏览器那侧没有构建步骤（design.md §9.1），也没有任何测试会执行这些脚本 ——
# lab/static 下的 .js 写错一个括号，其余几项全绿而页面上那排按钮一动不动。
# node --check 只解析不执行（不碰 DOM、不联网），正好补这个空档。
# node 是开发机上的工具、不是这个项目的依赖（design.md §3.2：不做构建链），
# 没装就明说跳过，不把「这台机器没有」算成「代码有问题」。
js_gate() {
    if ! command -v node >/dev/null 2>&1; then
        printf "没装 node，跳过 JS 语法检查（装了就会自动解析 lab/static/*.js）。\n"
        return 0
    fi
    for file in lab/static/*.js; do
        if ! node --check "$file"; then
            printf "%s 解析不过。\n" "$file" >&2
            return 1
        fi
    done
    printf "lab/static 下的 JS 都解析得过。\n"
}

# ruff 同理不带路径：不带就是「扫这个仓库」，scripts/ 也在内。以前写死 `check lab tests`，
# scripts/check_docs.py 里的 9 处超长行因此一直没人查 —— 而它自己还是门禁的一部分。
if [ "$#" -eq 0 ]; then
    set -- pytest ruff mypy docs js
fi

for name in "$@"; do
    case "$name" in
        pytest) step "pytest（全部测试）" "$PYTEST" -q ;;
        ruff)   step "ruff（静态检查）" "$RUFF" check ;;
        mypy)   step "mypy（类型检查）" mypy_gate ;;
        docs)   step "docs（文档与代码对账）" "$PY" scripts/check_docs.py ;;
        js)     step "js（浏览器那侧的语法）" js_gate ;;
        *)
            printf "不认识的门禁项：%s（可选 pytest / ruff / mypy / docs / js）\n" "$name" >&2
            exit 2
            ;;
    esac
done

if [ "$failed" -ne 0 ]; then
    printf "\n门禁没过：上面有红的一项，修完再跑一次。\n" >&2
    exit 1
fi

printf "\n门禁过了。\n"
