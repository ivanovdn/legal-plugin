FROM python:3.12-slim

WORKDIR /app

# System deps for building any wheels lacking manylinux builds (mirrors ../compliance-bot).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching. Slim runtime set.
COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt

# Copy the backend runtime code (see .dockerignore for exclusions).
COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
