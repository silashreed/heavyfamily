from typing import Dict, Any, Optional, List, Callable, Awaitable, Union
import logging
import json
import asyncio
import uuid
import time
from decimal import Decimal

from ..adapters.wallet_adapter import HeavyWalletAdapter
from ..bridge.websocket_server import WebSocketBridge
from src.metrics import MetricsCollector, time_wallet_operation

logger = logging.getLogger('heavy-elizaos')

class ElizaOSConnector:
    """
    Connector for ElizaOS integration with Heavy's wallet system.
    
    This class provides the necessary methods to interact with ElizaOS agents,
    handling requests and events related to wallet operations, balance queries,
    and transactions.
    """
    
    def __init__(
        self,
        wallet_adapter: HeavyWalletAdapter,
        websocket_bridge: WebSocketBridge
    ) -> None:
        """
        Initialize the ElizaOS connector.
        
        Args:
            wallet_adapter: Adapter for Heavy's wallet system
            websocket_bridge: WebSocket bridge for communication
        """
        self.wallet_adapter = wallet_adapter
        self.websocket_bridge = websocket_bridge
        
        # Agent registry: mapping of agent_id to wallet_id
        self.agent_registry: Dict[str, str] = {}
        
        # Register handlers for ElizaOS events
        self._register_handlers()
        
    def _register_handlers(self) -> None:
        """Register handlers for ElizaOS WebSocket events."""
        logger.info("Registering ElizaOS message handlers")
        
        # Register wallet handlers
        self.websocket_bridge.register_handler("wallet_create", self.handle_wallet_create)
        self.websocket_bridge.register_handler("wallet_import", self.handle_wallet_import)
        self.websocket_bridge.register_handler("wallet_get", self.handle_wallet_get)
        self.websocket_bridge.register_handler("wallet_list", self.handle_wallet_list)
        self.websocket_bridge.register_handler("wallet_balance", self.handle_wallet_balance)
        self.websocket_bridge.register_handler("wallet_transfer", self.handle_wallet_transfer)
        self.websocket_bridge.register_handler("wallet_trade", self.handle_wallet_trade)
        
        # Register system handlers
        self.websocket_bridge.register_handler("ping", self.handle_ping)
        self.websocket_bridge.register_handler("status", self.handle_status)
        
    async def handle_wallet_create(
        self, 
        message_type: str, 
        data: Dict[str, Any], 
        websocket: WebSocketServerProtocol
    ) -> None:
        """
        Handle wallet create request.
        
        Args:
            message_type: The message type
            data: The message data
            websocket: The WebSocket connection
        """
        try:
            network = data.get("network", "ethereum-goerli")
            request_id = data.get("request_id")
            
            with time_wallet_operation("create", network):
                wallet_data = await self.wallet_adapter.create_wallet(network)
                
            await self.websocket_bridge.send_response(
                websocket,
                "wallet_created",
                {
                    "wallet_id": wallet_data["wallet_id"],
                    "address": wallet_data["address"],
                    "network": network
                },
                request_id
            )
            
            # Update wallet count metric
            wallets = await self.wallet_adapter.list_wallets()
            network_wallets = [w for w in wallets if w.get("network") == network]
            MetricsCollector.set_wallet_count(network, len(network_wallets))
            
            logger.info(f"Created wallet on network {network}: {wallet_data['wallet_id']}")
            
        except Exception as e:
            logger.error(f"Error creating wallet: {str(e)}")
            MetricsCollector.record_wallet_operation("create", network, "error")
            await self.websocket_bridge.send_error(
                websocket,
                f"Failed to create wallet: {str(e)}",
                request_id
            )
    
    async def handle_wallet_import(
        self, 
        message_type: str, 
        data: Dict[str, Any], 
        websocket: WebSocketServerProtocol
    ) -> None:
        """
        Handle wallet import request.
        
        Args:
            message_type: The message type
            data: The message data
            websocket: The WebSocket connection
        """
        try:
            private_key = data.get("private_key")
            network = data.get("network", "ethereum-goerli")
            request_id = data.get("request_id")
            
            if not private_key:
                await self.websocket_bridge.send_error(
                    websocket,
                    "Missing private_key parameter",
                    request_id
                )
                return
                
            with time_wallet_operation("import", network):
                wallet_data = await self.wallet_adapter.import_wallet(private_key, network)
                
            await self.websocket_bridge.send_response(
                websocket,
                "wallet_imported",
                {
                    "wallet_id": wallet_data["wallet_id"],
                    "address": wallet_data["address"],
                    "network": network
                },
                request_id
            )
            
            # Update wallet count metric
            wallets = await self.wallet_adapter.list_wallets()
            network_wallets = [w for w in wallets if w.get("network") == network]
            MetricsCollector.set_wallet_count(network, len(network_wallets))
            
            logger.info(f"Imported wallet on network {network}: {wallet_data['wallet_id']}")
            
        except Exception as e:
            logger.error(f"Error importing wallet: {str(e)}")
            MetricsCollector.record_wallet_operation("import", network, "error") 
            await self.websocket_bridge.send_error(
                websocket,
                f"Failed to import wallet: {str(e)}",
                request_id
            )
    
    async def handle_wallet_get(
        self, 
        message_type: str, 
        data: Dict[str, Any], 
        websocket: WebSocketServerProtocol
    ) -> None:
        """
        Handle get wallet request.
        
        Args:
            message_type: The message type
            data: The message data
            websocket: The WebSocket connection
        """
        try:
            wallet_id = data.get("wallet_id")
            request_id = data.get("request_id")
            
            if not wallet_id:
                await self.websocket_bridge.send_error(
                    websocket,
                    "Missing wallet_id parameter",
                    request_id
                )
                return
                
            with time_wallet_operation("get", "any"):
                wallet_data = await self.wallet_adapter.get_wallet(wallet_id)
                
            if not wallet_data:
                await self.websocket_bridge.send_error(
                    websocket,
                    f"Wallet not found: {wallet_id}",
                    request_id
                )
                return
                
            await self.websocket_bridge.send_response(
                websocket,
                "wallet_details",
                wallet_data,
                request_id
            )
            
            logger.debug(f"Retrieved wallet details: {wallet_id}")
            
        except Exception as e:
            logger.error(f"Error getting wallet: {str(e)}")
            MetricsCollector.record_wallet_operation("get", "any", "error")
            await self.websocket_bridge.send_error(
                websocket,
                f"Failed to get wallet: {str(e)}",
                request_id
            )
    
    async def handle_wallet_list(
        self, 
        message_type: str, 
        data: Dict[str, Any], 
        websocket: WebSocketServerProtocol
    ) -> None:
        """
        Handle list wallets request.
        
        Args:
            message_type: The message type
            data: The message data
            websocket: The WebSocket connection
        """
        try:
            network = data.get("network")
            request_id = data.get("request_id")
            
            with time_wallet_operation("list", network or "all"):
                wallets = await self.wallet_adapter.list_wallets()
                
            if network:
                wallets = [w for w in wallets if w.get("network") == network]
                
            await self.websocket_bridge.send_response(
                websocket,
                "wallet_list",
                {"wallets": wallets},
                request_id
            )
            
            # Update wallet count metrics for each network
            networks = set(w.get("network", "unknown") for w in wallets)
            for net in networks:
                net_wallets = [w for w in wallets if w.get("network") == net]
                MetricsCollector.set_wallet_count(net, len(net_wallets))
                
            logger.debug(f"Listed {len(wallets)} wallets" + (f" for network {network}" if network else ""))
            
        except Exception as e:
            logger.error(f"Error listing wallets: {str(e)}")
            MetricsCollector.record_wallet_operation("list", network or "all", "error")
            await self.websocket_bridge.send_error(
                websocket,
                f"Failed to list wallets: {str(e)}",
                request_id
            )
    
    async def handle_wallet_balance(
        self, 
        message_type: str, 
        data: Dict[str, Any], 
        websocket: WebSocketServerProtocol
    ) -> None:
        """
        Handle get wallet balance request.
        
        Args:
            message_type: The message type
            data: The message data
            websocket: The WebSocket connection
        """
        try:
            wallet_id = data.get("wallet_id")
            token = data.get("token", "ETH")
            request_id = data.get("request_id")
            
            if not wallet_id:
                await self.websocket_bridge.send_error(
                    websocket,
                    "Missing wallet_id parameter",
                    request_id
                )
                return
                
            # Get wallet to determine network
            wallet = await self.wallet_adapter.get_wallet(wallet_id)
            if not wallet:
                await self.websocket_bridge.send_error(
                    websocket,
                    f"Wallet not found: {wallet_id}",
                    request_id
                )
                return
                
            network = wallet.get("network", "ethereum-goerli")
                
            with time_wallet_operation("balance", network):
                balance = await self.wallet_adapter.get_balance(wallet_id, token)
                
            await self.websocket_bridge.send_response(
                websocket,
                "wallet_balance",
                {
                    "wallet_id": wallet_id,
                    "token": token,
                    "balance": balance
                },
                request_id
            )
            
            # Update balance metric
            try:
                # Convert to float for the metric
                balance_float = float(balance)
                MetricsCollector.set_wallet_balance(wallet_id, token, network, balance_float)
            except (ValueError, TypeError):
                logger.warning(f"Could not convert balance to float for metrics: {balance}")
            
            logger.debug(f"Retrieved balance for wallet {wallet_id}, token {token}: {balance}")
            
        except Exception as e:
            logger.error(f"Error getting balance: {str(e)}")
            MetricsCollector.record_wallet_operation("balance", "unknown", "error")
            await self.websocket_bridge.send_error(
                websocket,
                f"Failed to get balance: {str(e)}",
                request_id
            )
    
    async def handle_wallet_transfer(
        self, 
        message_type: str, 
        data: Dict[str, Any], 
        websocket: WebSocketServerProtocol
    ) -> None:
        """
        Handle wallet transfer request.
        
        Args:
            message_type: The message type
            data: The message data
            websocket: The WebSocket connection
        """
        try:
            wallet_id = data.get("wallet_id")
            to_address = data.get("to_address")
            amount = data.get("amount")
            token = data.get("token", "ETH")
            request_id = data.get("request_id")
            
            if not all([wallet_id, to_address, amount]):
                await self.websocket_bridge.send_error(
                    websocket,
                    "Missing required parameters: wallet_id, to_address, and amount are required",
                    request_id
                )
                return
            
            # Get wallet to determine network
            wallet = await self.wallet_adapter.get_wallet(wallet_id)
            if not wallet:
                await self.websocket_bridge.send_error(
                    websocket,
                    f"Wallet not found: {wallet_id}",
                    request_id
                )
                return
                
            network = wallet.get("network", "ethereum-goerli")
                
            with time_wallet_operation("transfer", network):
                transaction = await self.wallet_adapter.transfer(
                    wallet_id, to_address, amount, token
                )
                
            await self.websocket_bridge.send_response(
                websocket,
                "wallet_transfer_initiated",
                {
                    "wallet_id": wallet_id,
                    "transaction_id": transaction.get("transaction_id"),
                    "to_address": to_address,
                    "amount": amount,
                    "token": token,
                    "status": transaction.get("status", "pending")
                },
                request_id
            )
            
            logger.info(
                f"Transfer initiated from wallet {wallet_id} to {to_address}: "
                f"{amount} {token}, tx: {transaction.get('transaction_id')}"
            )
            
        except Exception as e:
            logger.error(f"Error initiating transfer: {str(e)}")
            MetricsCollector.record_wallet_operation("transfer", "unknown", "error")
            await self.websocket_bridge.send_error(
                websocket,
                f"Failed to initiate transfer: {str(e)}",
                request_id
            )
    
    async def handle_wallet_trade(
        self, 
        message_type: str, 
        data: Dict[str, Any], 
        websocket: WebSocketServerProtocol
    ) -> None:
        """
        Handle wallet trade request.
        
        Args:
            message_type: The message type
            data: The message data
            websocket: The WebSocket connection
        """
        try:
            wallet_id = data.get("wallet_id")
            from_token = data.get("from_token")
            to_token = data.get("to_token")
            amount = data.get("amount")
            request_id = data.get("request_id")
            
            if not all([wallet_id, from_token, to_token, amount]):
                await self.websocket_bridge.send_error(
                    websocket,
                    "Missing required parameters: wallet_id, from_token, to_token, and amount are required",
                    request_id
                )
                return
            
            # Get wallet to determine network
            wallet = await self.wallet_adapter.get_wallet(wallet_id)
            if not wallet:
                await self.websocket_bridge.send_error(
                    websocket,
                    f"Wallet not found: {wallet_id}",
                    request_id
                )
                return
                
            network = wallet.get("network", "ethereum-goerli")
                
            with time_wallet_operation("trade", network):
                transaction = await self.wallet_adapter.trade(
                    wallet_id, from_token, to_token, amount
                )
                
            await self.websocket_bridge.send_response(
                websocket,
                "wallet_trade_initiated",
                {
                    "wallet_id": wallet_id,
                    "transaction_id": transaction.get("transaction_id"),
                    "from_token": from_token,
                    "to_token": to_token,
                    "amount": amount,
                    "status": transaction.get("status", "pending")
                },
                request_id
            )
            
            logger.info(
                f"Trade initiated for wallet {wallet_id}: "
                f"{amount} {from_token} -> {to_token}, tx: {transaction.get('transaction_id')}"
            )
            
        except Exception as e:
            logger.error(f"Error initiating trade: {str(e)}")
            MetricsCollector.record_wallet_operation("trade", "unknown", "error")
            await self.websocket_bridge.send_error(
                websocket,
                f"Failed to initiate trade: {str(e)}",
                request_id
            )
    
    async def handle_ping(
        self, 
        message_type: str, 
        data: Dict[str, Any], 
        websocket: WebSocketServerProtocol
    ) -> None:
        """
        Handle ping request.
        
        Args:
            message_type: The message type
            data: The message data
            websocket: The WebSocket connection
        """
        request_id = data.get("request_id")
        await self.websocket_bridge.send_response(
            websocket,
            "pong",
            {"timestamp": data.get("timestamp", 0)},
            request_id
        )
        
        logger.debug("Responded to ping")
    
    async def handle_status(
        self, 
        message_type: str, 
        data: Dict[str, Any], 
        websocket: WebSocketServerProtocol
    ) -> None:
        """
        Handle status request.
        
        Args:
            message_type: The message type
            data: The message data
            websocket: The WebSocket connection
        """
        request_id = data.get("request_id")
        
        # Get wallet counts by network
        wallets = await self.wallet_adapter.list_wallets()
        networks = set(w.get("network", "unknown") for w in wallets)
        network_counts = {}
        
        for network in networks:
            count = len([w for w in wallets if w.get("network") == network])
            network_counts[network] = count
        
        await self.websocket_bridge.send_response(
            websocket,
            "status",
            {
                "status": "healthy",
                "wallets": {
                    "total": len(wallets),
                    "by_network": network_counts
                },
                "connections": len(self.websocket_bridge.active_connections)
            },
            request_id
        )
        
        logger.debug("Responded to status request")
        
    async def broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Broadcast an event to all connected clients.
        
        Args:
            event_type: The event type
            data: The event data
        """
        message = {
            "type": event_type,
            "data": data,
            "id": str(uuid.uuid4())
        }
        
        await self.websocket_bridge.broadcast(message)
        logger.debug(f"Broadcasted event: {event_type}")

def cli_main() -> None:
    """Command-line entry point for the ElizaOS Agent."""
    import argparse
    import asyncio
    
    parser = argparse.ArgumentParser(description='ElizaOS Agent')
    parser.add_argument('--host', type=str, default='localhost', help='Host to connect to')
    parser.add_argument('--port', type=int, default=8765, help='Port to connect to')
    parser.add_argument('--network', type=str, default='ethereum-goerli', help='Network to use')
    args = parser.parse_args()
    
    asyncio.run(agent_main(args.host, args.port, args.network))

async def agent_main(host: str, port: int, network: str) -> None:
    """Main entry point for the ElizaOS Agent."""
    # Agent implementation will go here
    logger.info(f"ElizaOS Agent connecting to {host}:{port} on network {network}")
    # TODO: Implement agent functionality 