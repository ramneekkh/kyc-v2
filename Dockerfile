# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# --- Stage 1: FE Build ---
  FROM node:lts-alpine AS builder
  WORKDIR /app/frontend
  COPY frontend/package.json frontend/yarn.lock* frontend/package-lock.json* ./
  RUN npm install
  COPY frontend/ ./
  RUN npm run build
  
  # --- Stage 2: Setup Python Runtime Environment ---
  FROM python:3.12.7-slim AS python-base
  ENV PYTHONDONTWRITEBYTECODE 1
  ENV PYTHONUNBUFFERED 1
  ENV FLASK_APP=backend/app.py
  
  ENV FLASK_PORT=8080
  ENV STREAMLIT_PORT=8501
  ENV APP_ENV=docker
  
  # Set working directory for the backend/final app
  WORKDIR /app
  
  
  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt
  
  # Copy the built frontend from the builder stage
  COPY --from=builder /app/frontend/dist ./frontend/dist
  
  # Copy the backend Flask application code
  COPY backend ./backend
  COPY migrations ./migrations
  
  # Copy the entrypoint script and make it executable
  COPY entrypoint.sh .
  RUN chmod +x ./entrypoint.sh
  
  EXPOSE 8080
  
  # Run the entrypoint script when the container starts
  CMD ["./entrypoint.sh"]