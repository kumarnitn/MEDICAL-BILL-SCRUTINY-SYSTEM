FROM python:3.11-slim

# Install system dependencies for OCR and PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create runtime directories (uploads, bills, ocr output)
RUN mkdir -p data/uploads data/ocr_output data/processed/bills

# Make start script executable
RUN chmod +x start.sh

# Expose the PORT used by Render (defaults to 8080)
EXPOSE 8080

# Entrypoint
CMD ["./start.sh"]
