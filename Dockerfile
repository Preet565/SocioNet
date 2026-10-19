FROM node:22.22.0-alpine AS frontend-build

WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./

# An empty API URL makes the browser use the same origin as the bundled API.
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM python:3.12.8-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SOCIONET_DB_PATH=/app/data/socionet.sqlite3 \
    SOCIONET_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000 \
    SOCIONET_SERVE_FRONTEND=1

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend-build /src/frontend/dist ./static

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
