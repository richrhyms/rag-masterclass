# Backend service: FastAPI running inside a Python venv (NFR-2).
FROM python:3.12-slim

WORKDIR /app

# Create and use a venv inside the image so the backend runs in a venv even in
# the containerized environment (NFR-2), not just in local/bare-metal dev.
ENV VENV_PATH=/opt/venv
RUN python -m venv ${VENV_PATH}
ENV PATH="${VENV_PATH}/bin:${PATH}"

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
