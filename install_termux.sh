#!/data/data/com.termux/files/usr/bin/bash
# Termux (Android) installer. Every dependency is pure Python: nothing needs Rust or a C compiler.
set -e
cd "$(dirname "$0")"
pkg update -y && pkg upgrade -y
pkg install -y python git
python -m pip install --upgrade pip
export AIOHTTP_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 FROZENLIST_NO_EXTENSIONS=1 PROPCACHE_NO_EXTENSIONS=1 BUILD_EXTENSION=no
python -m pip install -r requirements.txt
if grep -q "^SLACK_BOT_TOKEN=..*" .env 2>/dev/null; then python -m pip install -r requirements-slack.txt; fi
python -c "import httpx, websockets, starlette, uvicorn, telegram, dotenv; print('All dependencies OK')"
[ -f .env ] || cp .env.example .env
echo "Done. Edit .env (nano .env), then: python -m bitunix_agent --check"
