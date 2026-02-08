FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy proto file and generate gRPC stubs
COPY demo.proto ./demo.proto
RUN python -m grpc_tools.protoc \
    -I. \
    --python_out=. \
    --grpc_python_out=. \
    demo.proto

# Copy load generator script
COPY loadgen.py .

# Run the load generator
CMD ["python", "-u", "loadgen.py"]
