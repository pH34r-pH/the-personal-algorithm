FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown appuser:appuser /data
USER appuser
ENV TPA_DATABASE=/data/personal-algorithm.sqlite3
EXPOSE 8000
CMD ["uvicorn", "personal_algorithm.application:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
