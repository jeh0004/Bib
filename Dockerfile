FROM python:3.12.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create non-root user and set ownership
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app appuser \
    && mkdir -p /data \
    && chown -R appuser:appgroup /app /data

USER appuser

VOLUME ["/data"]

ENV DATABASE=/data/vereinsbibliothek.db
ENV SECRET_KEY=change-me-in-production
ENV COOKIE_SECURE=false

EXPOSE 5000

CMD ["sh", "-c", \
     "python -c 'from app import init_db; init_db()' && \
      gunicorn \
        --bind 0.0.0.0:5000 \
        --workers 2 \
        --timeout 30 \
        --limit-request-line 4094 \
        --limit-request-fields 20 \
        --access-logfile - \
        app:app"]
