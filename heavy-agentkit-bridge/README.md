# Heavy-AgentKit-ElizaOS Bridge

A bridge service connecting Heavy with Coinbase's AgentKit SDK and ElizaOS, enabling wallet operations and crypto transactions via WebSockets.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Deployment](#deployment)
- [Monitoring](#monitoring)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

## Features

- WebSocket server for real-time communication with ElizaOS agents
- Integration with Coinbase's AgentKit SDK for wallet and transaction operations
- Support for multiple cryptocurrencies and networks
- Secure wallet storage and management
- Metrics collection and monitoring
- Production-ready deployment with Docker

## Installation

### Prerequisites

- Python 3.8+
- Poetry (for dependency management)

### Install from Source

```bash
# Clone the repository
git clone https://github.com/your-org/heavy-agentkit-bridge.git
cd heavy-agentkit-bridge

# Install dependencies
poetry install

# Or use pip
pip install -e .
```

### Install using Docker

```bash
# Pull the image
docker pull ghcr.io/your-org/heavy-agentkit-bridge:latest

# Or build locally
docker build -t heavy-agentkit-bridge .
```

## Configuration

### Initial Setup

Run the setup command to create configuration files and directories:

```bash
heavy-bridge --init-only
```

This creates:
- Configuration directory at `~/.heavy/config`
- Wallet storage directory at `~/.heavy/wallets`
- Backup directory at `~/.heavy/backups`
- Log directory at `~/.heavy/logs`
- Default configuration at `~/.heavy/config/config.json`
- Environment template at `~/.heavy/config/.env`

### Environment Variables

Edit the `.env` file to set your credentials and configuration:

```
# API Credentials
AGENTKIT_API_KEY=your_api_key_here
AGENTKIT_API_SECRET=your_api_secret_here

# Security Configuration
WALLET_ENCRYPTION_KEY=generate_a_secure_random_string
CREDENTIAL_ENCRYPTION_KEY=generate_a_different_secure_random_string

# Network Configuration
HEAVY_NETWORK_ID=ethereum-goerli  # or ethereum-mainnet, solana-devnet, etc.

# Monitoring Configuration
ENABLE_METRICS=true
METRICS_PORT=8000
```

### Configuration Options

Edit `~/.heavy/config/config.json` to customize the bridge:

```json
{
  "websocket_host": "0.0.0.0",
  "websocket_port": 8765,
  "metrics_port": 8000,
  "enable_encryption": true,
  "wallet_dir": "~/.heavy/wallets",
  "backup_dir": "~/.heavy/backups",
  "config_dir": "~/.heavy/config",
  "log_dir": "~/.heavy/logs",
  "network_id": "ethereum-goerli",
  "use_encrypted_storage": false
}
```

## API Reference

### WebSocket API

Connect to the WebSocket server at `ws://host:port` (default: `ws://localhost:8765`).

#### Wallet Operations

| Message Type | Description | Parameters |
|--------------|-------------|------------|
| `wallet_create` | Create a new wallet | `network` (optional) |
| `wallet_import` | Import an existing wallet | `private_key`, `network` (optional) |
| `wallet_get` | Get wallet details | `wallet_id` |
| `wallet_list` | List all wallets | `network` (optional) |
| `wallet_balance` | Get wallet balance | `wallet_id`, `token` (optional) |
| `wallet_transfer` | Transfer tokens | `wallet_id`, `to_address`, `amount`, `token` (optional) |
| `wallet_trade` | Trade tokens | `wallet_id`, `from_token`, `to_token`, `amount` |

#### System Operations

| Message Type | Description | Parameters |
|--------------|-------------|------------|
| `ping` | Ping the server | `timestamp` (optional) |
| `status` | Get server status | None |

### Response Format

All responses follow this format:

```json
{
  "type": "response_type",
  "data": { ... },
  "request_id": "original_request_id",
  "timestamp": 1617293932.123
}
```

## Deployment

### Using Docker Compose

The easiest way to deploy the bridge is using Docker Compose:

```bash
# Start the service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the service
docker-compose down
```

### Manual Deployment

Run the bridge service manually:

```bash
# Start the bridge
heavy-bridge

# With custom configuration
heavy-bridge --config /path/to/config.json --env-file /path/to/.env
```

### Systemd Service (Linux)

Create a systemd service file at `/etc/systemd/system/heavy-bridge.service`:

```ini
[Unit]
Description=Heavy AgentKit Bridge
After=network.target

[Service]
User=heavy
Group=heavy
WorkingDirectory=/opt/heavy-agentkit-bridge
ExecStart=/usr/local/bin/heavy-bridge
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl enable heavy-bridge
sudo systemctl start heavy-bridge
```

## Monitoring

### Metrics

The bridge exposes Prometheus metrics on `http://host:metrics_port/metrics` (default: `http://localhost:8000/metrics`).

Available metrics:

- `wallet_operations_total`: Count of wallet operations by type, network, and status
- `wallet_count`: Number of wallets by network
- `wallet_balance`: Wallet balances by token
- `request_latency_seconds`: Request latency histograms
- `active_connections`: Number of active WebSocket connections
- `api_requests_total`: Count of API requests
- `cdp_api_calls_total`: Count of CDP API calls

### Health Check

A health check endpoint is available at `http://host:metrics_port/health`.

### Prometheus and Grafana

The Docker Compose setup includes Prometheus and Grafana for monitoring:

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000` (default login: admin/admin)

### Logging

Logs are written to `~/.heavy/logs/heavy-bridge.log` and to stdout. In Docker, logs are captured in the container logs.

## Security

### Credential Management

Credentials are securely managed with the following features:

- Stored in environment variables or encrypted storage
- Support for credential rotation
- Automatic backup of credentials during rotation

### Wallet Security

Wallet data is protected using:

- Encryption of sensitive wallet data
- Secure deletion of files
- Wallet data isolation
- Regular backups

### Best Practices

- Run as a non-root user in production
- Use encrypted connections for WebSockets in production
- Rotate API keys regularly
- Keep the service updated with security patches

## Troubleshooting

### Common Issues

#### Unable to Connect to WebSocket

- Check if the service is running with `ps aux | grep heavy-bridge`
- Verify the WebSocket port is open with `netstat -tuln | grep 8765`
- Check firewall settings

#### API Key Issues

- Verify your API key and secret are correctly set in the `.env` file
- Check logs for authentication errors
- Try rotating your API credentials

#### Wallet Operations Failing

- Ensure network connectivity to AgentKit services
- Verify you have sufficient balance for operations
- Check transaction parameters are valid

### Logs

Check logs for detailed error information:

```bash
# View logs
tail -f ~/.heavy/logs/heavy-bridge.log

# In Docker
docker logs heavy-bridge
```

## License

Proprietary. Copyright © 2024 Heavy Team. All rights reserved.
