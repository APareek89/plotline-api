#!/usr/bin/env bash
# Start api :8600 + rag :8788 + devrag :8787 DETACHED, so they survive the shell
# that launched them.
#
# WHY THIS EXISTS, separately from run.sh:
#   run.sh runs uvicorn in the foreground and traps EXIT to kill devrag, which is
#   right for a terminal you are watching — Ctrl-C stops everything. But it means
#   the whole stack dies with its shell, and an agent session that starts it from
#   a tool call takes all three down when that session ends. The symptom is
#   "Failed to fetch" in the browser with no other clue.
#
#   Each service here is started in its OWN session (setsid via a double fork),
#   so they are independent: one dying does not take the others with it.
#
# Usage:   bash run-detached.sh          # start whatever is not already up
#          bash run-detached.sh --stop   # stop all three
#          bash run-detached.sh --status # what is listening
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"

status() {
  for p in 8600 8788 8787 3100; do
    name=$(case $p in 8600) echo "api    ";; 8788) echo "rag    ";; 8787) echo "devrag ";; 3100) echo "web    ";; esac)
    if lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1; then
      echo "  $name :$p  UP"
    else
      echo "  $name :$p  down"
    fi
  done
}

if [ "$1" = "--status" ]; then status; exit 0; fi

if [ "$1" = "--stop" ]; then
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "uvicorn devrag.server:app" 2>/dev/null || true
  pkill -f "plotline_rag" 2>/dev/null || true
  sleep 1
  echo "stopped (web on :3100 is left alone — it is a separate repo)"
  status
  exit 0
fi

"$ROOT/.venv/bin/python" - "$ROOT" <<'PY'
import os, sys, socket, time

root = sys.argv[1]
venv = f"{root}/.venv/bin"

def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0

JOBS = [
    (8788, "rag",    ["make", "serve-s3", "PORT=8788"], f"{root}/rag", "/tmp/plotline-rag.log"),
    (8787, "devrag", [f"{venv}/uvicorn", "devrag.server:app", "--host", "127.0.0.1", "--port", "8787"], root, "/tmp/plotline-devrag.log"),
    (8600, "api",    [f"{venv}/uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8600"], root, "/tmp/plotline-api.log"),
]

started = []
for port, name, cmd, cwd, log in JOBS:
    if listening(port):
        print(f"  {name}: already up on :{port}")
        continue
    started.append((port, name))
    if os.fork() != 0:
        continue
    os.setsid()                       # its own session — outlives this shell
    if os.fork() != 0:
        os._exit(0)                   # double fork: no controlling terminal
    fh = open(log, "ab", buffering=0)
    os.dup2(fh.fileno(), 1)
    os.dup2(fh.fileno(), 2)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    os.chdir(cwd)
    os.execvp(cmd[0], cmd)

for port, name in started:
    for _ in range(60):
        if listening(port):
            print(f"  {name}: started on :{port}")
            break
        time.sleep(0.5)
    else:
        print(f"  {name}: DID NOT COME UP on :{port} — see the log in /tmp")
PY

echo ""
echo "logs: /tmp/plotline-{api,rag,devrag}.log"
status
echo ""
# This script does NOT start the web server — it only reports it. Saying "run
# npm run dev" while web is already UP sends the reader straight into an
# EADDRINUSE they then have to interpret. Say which of the two situations they
# are actually in.
if lsof -ti :3100 >/dev/null 2>&1; then
  echo "The web app is ALREADY running → http://localhost:3100"
  echo "(so 'npm run dev' will exit with EADDRINUSE — that means 'already up', not broken)"
else
  echo "The web app is a separate repo: cd ../plotline-web && npm run dev   (:3100)"
fi
