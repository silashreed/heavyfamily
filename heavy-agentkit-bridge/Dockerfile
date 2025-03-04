FROM python:3.10-slim AS builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev libffi-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry

# Copy project files
COPY pyproject.toml README.md ./

# Configure Poetry to not create a virtual environment
RUN poetry config virtualenvs.create false

# Install dependencies only (without project)
RUN poetry install --no-dev --no-root

# Build stage
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HEAVY_HOME=/home/heavy \
    HEAVY_CONFIG_DIR=/home/heavy/config \
    HEAVY_WALLETS_DIR=/home/heavy/wallets \
    HEAVY_LOGS_DIR=/home/heavy/logs

# Create non-root user
RUN groupadd -g 1000 heavy && \
    useradd -u 1000 -g heavy -s /bin/bash -m heavy

# Create necessary directories
RUN mkdir -p ${HEAVY_CONFIG_DIR} ${HEAVY_WALLETS_DIR} ${HEAVY_LOGS_DIR} && \
    chown -R heavy:heavy ${HEAVY_HOME}

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends tini && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy files from builder
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.10/site-packages/ /usr/local/lib/python3.10/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copy application code
COPY . .

# Set permissions
RUN chown -R heavy:heavy /app

# Switch to non-root user
USER heavy

# Create default config directories
RUN mkdir -p ~/.heavy/config ~/.heavy/wallets

# Expose WebSocket port
EXPOSE 8765

# Use tini as init system to handle signals properly
ENTRYPOINT ["/usr/bin/tini", "--"]

# Run the application
CMD ["python", "-m", "src.main"] 