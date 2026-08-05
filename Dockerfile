# ---- 构建前端（React + Vite）----
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 运行时（FastAPI 后端，静态托管前端产物）----
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    TZ=Asia/Shanghai \
    # 配置与同步记录目录（挂载持久化）
    CANON_NAS_DATA=/data

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /build/dist ./app/static

EXPOSE 8315
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8315"]
