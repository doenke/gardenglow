FROM python:3.12-slim
WORKDIR /app

ARG APP_VERSION=dev
ARG GIT_COMMIT=unknown

ENV APP_VERSION=$APP_VERSION
ENV GIT_COMMIT=$GIT_COMMIT

LABEL org.opencontainers.image.title="GardenGlow" \
      org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$GIT_COMMIT \
      org.opencontainers.image.source="https://github.com/doenke/garten"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV FLASK_ENV=production
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["python", "run.py"]
