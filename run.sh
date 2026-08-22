#!/usr/bin/env bash
# Starts plotline-rag (8788, if not already up) + dev RAG stub (8787, aux
# corpora: asset:/stat:/trend:) + plotline-api (8600)
set -e
cd "$(dirname "$0")"
source .venv/bin/activate

RAG_URL="${PLOTLINE_RAG_URL:-http://127.0.0.1:8788}"
if ! curl -sf -m 2 "$RAG_URL/health" > /dev/null 2>&1; then
  echo "starting plotline-rag (in-repo, rag/) on 8788 (serve-s3)…"
  (cd rag && make serve-s3 PORT=8788 > /tmp/plotline-rag.log 2>&1 &)
fi

uvicorn devrag.server:app --host 127.0.0.1 --port 8787 &
DEVRAG_PID=$!
trap "kill $DEVRAG_PID 2>/dev/null" EXIT
uvicorn app.main:app --host 127.0.0.1 --port 8600
