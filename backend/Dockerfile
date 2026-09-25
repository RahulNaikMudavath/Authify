FROM python:3.11-slim

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PORT=5000
ENV MONGO_URI=mongodb://mongo:27017/
ENV REDIS_URL=redis://redis:6379/0

EXPOSE 5000

# Script to seed database on first boot if empty, then start server
CMD ["python", "entrypoint.py"]
