#!/usr/bin/env bash
# Starts the dev RAG stub (8787) + plotline-api (8600)
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
uvicorn devrag.server:app --host 127.0.0.1 --port 8787 &
DEVRAG_PID=$!
trap "kill $DEVRAG_PID 2>/dev/null" EXIT
uvicorn app.main:app --host 127.0.0.1 --port 8600
