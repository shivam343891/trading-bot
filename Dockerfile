FROM python:3.12-slim

# Install OS deps for pandas_ta / compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create runtime directories
RUN mkdir -p logs storage

# Expose Streamlit dashboard port
EXPOSE 8501

# Default: run the bot. Override CMD to run dashboard instead.
CMD ["python", "main.py"]
