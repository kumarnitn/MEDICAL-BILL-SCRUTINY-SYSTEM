#!/bin/bash
set -e

# Default PORT if not set
PORT="${PORT:-8080}"

echo "Starting deployment checks..."

# Check if database exists, if not, try to initialize it
if [ ! -f "data/processed/medical_bills.db" ]; then
    echo "Database not found. Initializing..."
    
    # Create directories if they don't exist
    mkdir -p data/processed data/raw data/ocr_output
    
    # Run parsers if source PDFs exist
    if [ -f "CGHS Rate.pdf" ]; then
        echo "Parsing CGHS Rates..."
        python scripts/parse_cghs_rates.py
    else
        echo "Warning: 'CGHS Rate.pdf' not found. Skipping rate parsing."
    fi
    
    if [ -f "Hospital List 08.10.2025 (1).pdf" ]; then
        echo "Parsing Hospital List..."
        # PDfToText might be needed here, ensure poppler-utils is installed in Dockerfile
        python scripts/parse_hospital_list.py
    else
        echo "Warning: Hospital list PDF not found. Skipping hospital parsing."
    fi
    
    echo "Setting up database schema..."
    python scripts/setup_database.py
else
    echo "Database found. Skipping initialization."
fi

echo "Starting server on port $PORT..."
exec uvicorn server:app --host 0.0.0.0 --port "$PORT"
