#!/usr/bin/env bash
# 构建前端并打入 Python 包：先执行 build-ui.sh，再用 PEP 517 构建 (python -m build)。
# 在仓库根目录执行: ./scripts/build-and-package.sh [--install]
# 若传入 --install，会在构建后执行 pip install -e .

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DO_INSTALL=false
for arg in "$@"; do
  if [ "$arg" = "--install" ]; then
    DO_INSTALL=true
    break
  fi
done

echo "==> 1. 构建前端并复制到 joyhousebot/static/ui"
"$ROOT/scripts/build-ui.sh"

echo ""
echo "==> 2. 构建 Python 包 (python -m build)"
if command -v uv &>/dev/null; then
  uv run --with build python -m build
else
  python3 -m build
fi

if [ "$DO_INSTALL" = true ]; then
  echo ""
  echo "==> 3. 可编辑安装 (pip install -e .)"
  if command -v uv &>/dev/null; then
    uv pip install -e .
  else
    pip install -e .
  fi
  echo "Done. 可执行 joyhousebot gateway 后访问 http://<host>:<port>/ui/"
else
  echo ""
  echo "Done. 如需本地安装可执行: pip install -e .  或重新运行本脚本并加 --install"
fi
