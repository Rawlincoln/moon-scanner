#!/usr/bin/env bash
set -e
echo "Starting Moon Scanner on port ${PORT:-10000}"
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-10000}" --timeout-keep-alive 120