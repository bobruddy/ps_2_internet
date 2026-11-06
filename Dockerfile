FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

VOLUME ["/app/data"]

EXPOSE 8080
CMD ["uvicorn", "ps_internet.web:app", "--host", "0.0.0.0", "--port", "8080"]
