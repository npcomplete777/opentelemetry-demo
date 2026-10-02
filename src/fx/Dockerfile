# fx service. BASE selects the Python image so the same code can run on an
# image whose libpython keeps its symbol table (python:3.12) and one that is
# stripped (python:3.12-slim). No telemetry packages are installed.
ARG BASE=python:3.12
FROM ${BASE}
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -c "import uvloop"
# Self-signed certificate for the demo's HTTPS listener.
RUN python - <<'PY'
import subprocess
subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
  "-keyout", "/app/key.pem", "-out", "/app/cert.pem", "-days", "3650",
  "-subj", "/CN=fx", "-addext",
  "subjectAltName=DNS:fx-full,DNS:fx-slim,DNS:localhost"], check=True)
PY
COPY app.py .
EXPOSE 8443
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8443", "--loop", "uvloop", "--http", "h11", "--ssl-keyfile", "/app/key.pem", "--ssl-certfile", "/app/cert.pem"]
