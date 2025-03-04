#!/usr/bin/env python3
import asyncio
import logging
import os
import json
import argparse
import signal
from pathlib import Path
from typing import Dict, Any, Optional, List

from dotenv import load_dotenv

from .bridge.websocket_server import WebSocketServer
from .bridge.agentkit_bridge import AgentKitBridge, CredentialManager
from .adapters.wallet_adapter import HeavyWalletAdapter, WalletStateManager, WalletSecurityManager
from .elizaos.elizaos_connector import ElizaOSConnector
from .metrics import start_metrics_service, MetricsCollector, setup_default_metrics

# Default paths
DEFAULT_CONFIG_DIR = os.path.expanduser("~/.heavy/config")
DEFAULT_CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, "bridge_config.json")
DEFAULT_ENV_TEMPLATE = os.path.join(DEFAULT_CONFIG_DIR, ".env.template")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(
            os.getenv('HEAVY_LOG_DIR', os.path.expanduser('~/.heavy/logs')), 
            'heavy-bridge.log'
        )),
    ]
)
logger = logging.getLogger('heavy-bridge')

DEFAULT_CONFIG = {
    "websocket_host": "0.0.0.0",
    "websocket_port": 8765,
    "metrics_port": 8000,
    "enable_encryption": True,
    "wallet_dir": os.path.expanduser("~/.heavy/wallets"),
    "backup_dir": os.path.expanduser("~/.heavy/backups"),
    "config_dir": os.path.expanduser("~/.heavy/config"),
    "log_dir": os.path.expanduser("~/.heavy/logs"),
    "network_id": "ethereum-goerli",
    "use_encrypted_storage": False,
}

def ensure_dir_exists(directory: str) -> None:
    """Ensure that the given directory exists."""
    path = Path(directory)
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created directory: {directory}")

def create_default_config(config_path: str) -> None:
    """Create a default configuration file if it doesn't exist."""
    if not os.path.exists(config_path):
        with open(config_path, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        logger.info(f"Created default configuration at {config_path}")

def create_env_template(env_path: str) -> None:
    """Create a template .env file if it doesn't exist."""
    if not os.path.exists(env_path):
        with open(env_path, 'w') as f:
            f.write("""# Heavy-AgentKit-Bridge Environment Configuration
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
""")
        logger.info(f"Created template .env file at {env_path}")
        logger.warning("Please edit the .env file with your actual credentials before running the application.")

async def shutdown(websocket_server, metrics_service) -> None:
    """Gracefully shutdown all services."""
    logger.info("Shutting down services...")
    
    if websocket_server:
        await websocket_server.stop()
        logger.info("WebSocket server stopped.")
    
    if metrics_service:
        await metrics_service.stop()
        logger.info("Metrics service stopped.")
    
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All tasks cancelled.")
    
    asyncio.get_event_loop().stop()
    logger.info("Event loop stopped.")

async def main(config: Dict[str, Any]) -> None:
    """Main entry point for the Heavy-AgentKit-Bridge."""
    logger.info("Starting Heavy-AgentKit-Bridge...")
    logger.info(f"Using network: {config['network_id']}")

    # Setup metrics
    enable_metrics = os.getenv('ENABLE_METRICS', 'true').lower() == 'true'
    metrics_port = int(os.getenv('METRICS_PORT', config.get('metrics_port', 8000)))
    metrics_service = None
    
    if enable_metrics:
        setup_default_metrics()
        metrics_service = await start_metrics_service(
            host=config.get('websocket_host', '0.0.0.0'),
            port=metrics_port
        )
        logger.info(f"Metrics server started on port {metrics_port}")
    else:
        logger.info("Metrics collection is disabled")

    # Initialize security and state managers
    wallet_encryption_key = os.getenv('WALLET_ENCRYPTION_KEY')
    enable_encryption = config.get('enable_encryption', True) and wallet_encryption_key is not None
    
    if enable_encryption and not wallet_encryption_key:
        logger.warning("Wallet encryption is enabled but no encryption key provided. Using default key (NOT SECURE).")
        wallet_encryption_key = "default_insecure_key_please_change_in_production"
    
    wallet_security_manager = WalletSecurityManager(
        enable_encryption=enable_encryption,
        encryption_key=wallet_encryption_key
    )
    
    wallet_state_manager = WalletStateManager(
        wallet_dir=config.get('wallet_dir'),
        backup_dir=config.get('backup_dir'),
        security_manager=wallet_security_manager
    )
    
    # Initialize credential manager
    credential_manager = CredentialManager(
        use_encrypted_storage=config.get('use_encrypted_storage', False),
        encryption_key=os.getenv('CREDENTIAL_ENCRYPTION_KEY'),
        config_dir=config.get('config_dir')
    )

    # Initialize AgentKit Bridge
    agentkit_bridge = AgentKitBridge(
        api_key=os.getenv('AGENTKIT_API_KEY'),
        api_secret=os.getenv('AGENTKIT_API_SECRET'),
        network_id=config.get('network_id'),
        credential_manager=credential_manager
    )
    
    # Initialize Wallet Adapter
    wallet_adapter = HeavyWalletAdapter(
        bridge=agentkit_bridge,
        state_manager=wallet_state_manager,
        security_manager=wallet_security_manager
    )
    
    # Initialize WebSocket Server
    websocket_server = WebSocketServer(
        host=config.get('websocket_host', '0.0.0.0'),
        port=config.get('websocket_port', 8765)
    )
    
    # Initialize ElizaOS Connector
    elizaos_connector = ElizaOSConnector(
        wallet_adapter=wallet_adapter,
        websocket_server=websocket_server
    )
    
    # Register handlers
    elizaos_connector.register_handlers()
    
    # Start WebSocket Server
    await websocket_server.start()
    logger.info(f"WebSocket server started on {config.get('websocket_host', '0.0.0.0')}:{config.get('websocket_port', 8765)}")
    
    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(shutdown(websocket_server, metrics_service))
        )
    
    try:
        # Keep the main task running
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour
    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    finally:
        await shutdown(websocket_server, metrics_service)

def cli_main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description='Heavy-AgentKit-Bridge Service')
    parser.add_argument('--config', type=str, help='Path to configuration file')
    parser.add_argument('--env-file', type=str, help='Path to .env file')
    parser.add_argument('--log-level', type=str, default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        help='Set the logging level')
    parser.add_argument('--init-only', action='store_true', help='Only initialize directories and config files, then exit')
    args = parser.parse_args()
    
    # Set log level
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    
    # Load environment variables
    env_file = args.env_file or os.path.join(
        os.path.expanduser(DEFAULT_CONFIG['config_dir']), 
        '.env'
    )
    load_dotenv(env_file)
    
    # Create necessary directories
    for dir_key in ['config_dir', 'wallet_dir', 'backup_dir', 'log_dir']:
        ensure_dir_exists(os.path.expanduser(DEFAULT_CONFIG[dir_key]))
    
    # Create default config file if it doesn't exist
    config_path = args.config or os.path.join(
        os.path.expanduser(DEFAULT_CONFIG['config_dir']), 
        'config.json'
    )
    create_default_config(config_path)
    
    # Create template .env file if it doesn't exist
    create_env_template(env_file)
    
    # Load configuration
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    # Override config with environment variables
    if os.getenv('HEAVY_NETWORK_ID'):
        config['network_id'] = os.getenv('HEAVY_NETWORK_ID')
    
    if args.init_only:
        logger.info("Initialization complete. Exiting.")
        return
    
    # Run the main async function
    try:
        asyncio.run(main(config))
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt. Exiting.")
    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")
        sys.exit(1)

if __name__ == "__main__":
    cli_main() 