FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install TradingAgents dependencies (multi-agent trading framework)
COPY ta_requirements.txt /tmp/ta_requirements.txt
RUN pip install --no-cache-dir -r /tmp/ta_requirements.txt && rm /tmp/ta_requirements.txt

# Copy application code
COPY backend/ .

# Create data and logs directories
RUN mkdir -p /app/data /app/logs

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run with Gunicorn
CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
