#!/usr/bin/env bash
# pytimefolio 저장소를 src 레이아웃으로 정리한다. 저장소 루트에서 한 번만 실행.
set -euo pipefail
cd "$(dirname "$0")"
[ -d _restructure ] || { echo "_restructure 폴더가 없음"; exit 1; }
mvg() { git mv "$1" "$2" 2>/dev/null || mv "$1" "$2"; }   # 추적 중이면 git mv, 아니면 mv

mkdir -p src docs
[ -d pytimefolio ] && mvg pytimefolio src/pytimefolio
[ -d scripts ]     && mvg scripts research
[ -d report ]      && mvg report docs/report
[ -f docs/report/order_api_capture.md ] && mvg docs/report/order_api_capture.md docs/api.md

# 루트의 try_*.py 와 예전 단위 테스트는 examples/, tests/live/, tests/test_*.py 로 대체된다
git rm -q --cached try_*.py tests/test_offline.py 2>/dev/null || true
rm -f try_*.py tests/test_offline.py
find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -f docs/report/*.bak docs/report/*.aux docs/report/*.log docs/report/*.out

cp -r _restructure/. .
rm -rf _restructure

# CI 설정 (원격 도구로는 .github 에 쓸 수 없어서 여기서 만든다)
mkdir -p .github/workflows
cat > .github/workflows/ci.yml <<'CI'
name: tests
on: [push, pull_request]
jobs:
  offline:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: pip install -e .
      - run: python -m unittest discover -v tests     # offline only; tests/live is never run in CI
CI

# 개인 메모는 공개 저장소에 올리지 않는다
git rm -q --cached docs/memo.md 2>/dev/null || true

cat >> .gitignore <<'G'
docs/memo.md
*.egg-info/
build/
dist/
docs/report/*.aux
docs/report/*.log
docs/report/*.out
docs/report/*.bak
docs/report/pg-*.png
G

.venv/bin/pip install -q -e .
.venv/bin/python -m unittest discover tests
git add -A
echo; echo "== git status (커밋 전에 .env, data/, logs/ 가 없는지 확인) =="
git status --short
