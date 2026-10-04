#!/usr/bin/env bash
set -euo pipefail

for argument in "$@"; do
    case "$argument" in
        -h|--help)
            cat <<'HELP'
用法：bash start-research.sh [参数]

  --port PORT       本地服务端口，默认 4381
  --source PATH     论文检索结构的来源目录
  --state-dir PATH  任务和配置保存目录，默认项目 .research-state
  --workers COUNT   并行 worker 数量，默认 30
  -h, --help        显示帮助；不创建环境或安装依赖

使用项目 .venv，要求 Python 3.10 或更高版本。
可通过 RESEARCH_SWARM_PYTHON 指定首次创建环境的 Python 可执行文件。
默认使用已提交的 dist，启动不需要 Node.js。
服务在前台运行，按 Ctrl+C 停止，不自动打开浏览器。
相对路径参数以项目目录为基准。
HELP
            exit 0
            ;;
    esac
done

fail() {
    printf '启动失败：%s\n' "$*" >&2
    exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd -- "$SCRIPT_DIR"

[[ -f "$SCRIPT_DIR/dist/index.html" ]] || fail '缺少 dist/index.html。请使用包含预构建界面的完整版本，或安装 Node.js 后执行 npm ci 和 npm run build。'
[[ -f "$SCRIPT_DIR/requirements.txt" ]] || fail '缺少 requirements.txt，请检查项目是否完整。'

VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VERSION_CHECK='import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'

if [[ ! -x "$VENV_PYTHON" ]]; then
    [[ ! -e "$VENV_DIR" ]] || fail '项目 .venv 不完整。请修复该环境，或将 .venv 重命名后重试；需要 Python 3.10+ 的 venv 和 pip 支持。'
    if [[ -n "${RESEARCH_SWARM_PYTHON:-}" ]]; then
        BASE_PYTHON="$RESEARCH_SWARM_PYTHON"
    elif command -v python3 >/dev/null 2>&1; then
        BASE_PYTHON=python3
    elif command -v python >/dev/null 2>&1; then
        BASE_PYTHON=python
    else
        fail '未找到 Python。请安装 Python 3.10+，并启用 venv 和 pip 支持。'
    fi
    command -v "$BASE_PYTHON" >/dev/null 2>&1 || fail '指定的 Python 可执行文件不存在，请检查 RESEARCH_SWARM_PYTHON；要求 Python 3.10+。'
    "$BASE_PYTHON" -c "$VERSION_CHECK" || fail 'Python 版本必须为 3.10 或更高。请安装兼容版本或设置 RESEARCH_SWARM_PYTHON。'
    "$BASE_PYTHON" -m venv "$VENV_DIR" || fail '无法创建项目 .venv。请为当前 Python 安装 venv / ensurepip 支持；Debian 或 Ubuntu 通常需要 python3-venv。'
fi

"$VENV_PYTHON" -c "$VERSION_CHECK" || fail '项目 .venv 的 Python 必须为 3.10+。请修复该环境，或将 .venv 重命名后用兼容 Python 重建。'
"$VENV_PYTHON" -m pip --version >/dev/null 2>&1 || fail '项目 .venv 缺少 pip。请修复 Python 的 venv / ensurepip 支持，或在该环境中执行 python -m ensurepip --upgrade。'
"$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt" || fail '项目 pip 安装 requirements.txt 失败，请检查网络、包源及 Python 的 pip 支持；未启动服务。'

export PYTHONIOENCODING=utf-8
exec "$VENV_PYTHON" -X utf8 -m research_swarm "$@"
