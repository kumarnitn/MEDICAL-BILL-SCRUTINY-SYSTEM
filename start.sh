#!/bin/bash
set -e

# Default PORT if not set (Render sets $PORT automatically)
PORT="${PORT:-8080}"

echo "============================================================"
echo "  MedBill AI — Startup"
echo "============================================================"

# Ensure runtime directories exist
mkdir -p data/uploads data/ocr_output data/processed/bills

# Initialize database if it doesn't exist
if [ ! -f "data/processed/medical_bills.db" ]; then
    echo "[INIT] Database not found. Running setup..."
    python scripts/setup_database.py
    echo "[INIT] Database created."
else
    echo "[OK]   Database already exists ($(du -sh data/processed/medical_bills.db | cut -f1))"
fi

echo "[OK]   Starting server on port $PORT..."
exec uvicorn server:app --host 0.0.0.0 --port "$PORT" --log-level info
