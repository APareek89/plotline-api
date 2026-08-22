#!/usr/bin/env bash
# Render deploy: mock mode, devrag serves the frozen retrieval contract as BOTH
# primary and aux (sample data — the honest cloud demo; no AWS, no LLM key).
set -e
uvicorn devrag.server:app --host 127.0.0.1 --port 8787 &
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8600}"
