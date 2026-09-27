# One image: built frontend served by the FastAPI backend on :8000.
# Local models (torch) are opt-in:  docker build --build-arg EXTRAS=local .
FROM node:22-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ARG EXTRAS=""
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-cache-dir -e ".${EXTRAS:+[$EXTRAS]}"
COPY backend/presets ./presets
COPY backend/data/csv ./data/csv
COPY --from=web /app/frontend/dist /app/frontend/dist
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
