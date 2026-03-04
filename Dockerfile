FROM python:3.11-slim

# Install system dependencies for PaddleOCR (OpenCV) and PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    poppler-utils \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create runtime directories
RUN mkdir -p data/uploads data/ocr_output data/processed/bills

RUN chmod +x start.sh

# Koyeb uses port 8000 by default, but we read from $PORT
EXPOSE 8000

CMD ["./start.sh"]
