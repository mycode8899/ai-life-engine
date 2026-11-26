FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
RUN python -m pip install --no-cache-dir .

VOLUME /data
EXPOSE 8765
CMD ["ai-life-engine", "serve", "--host", "0.0.0.0", "--port", "8765", "--allow-remote", "--data-dir", "/data"]
