#!/usr/bin/env python3
import asyncio
import logging
import json
import os
import argparse
import uuid
import websockets
from decimal import Decimal
from typing import Dict, Any, Optional, List, Union, Callable, Awaitable
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("heavy.elizaos_agent")

class HeavyAgent:
    """
    Heavy Agent for ElizaOS that connects to the AgentKit Bridge.
    
    This agent provides wallet operations through the AgentKit Bridge service,
    enabling ElizaOS to interact with blockchain operations via Coinbase's
    AgentKit SDK.
    """
    
    def __init__(
        self,
        bridge_url: str,
        agent_id: Optional[str] = None,
        wallet_id: Optional[str] = None,
        agent_name: Optional[str] = None
    ) -> None:
        """
        Initialize the Heavy Agent.
        
        Args:
            bridge_url: URL of the AgentKit Bridge WebSocket service
            agent_id: Optional agent ID (generated if not provided)
            wallet_id: Optional wallet ID to associate with this agent
            agent_name: Optional agent name
        """
        self.bridge_url = bridge_url
        self.agent_id = agent_id or str(uuid.uuid4())
        self.wallet_id = wallet_id
        self.agent_name = agent_name or f"heavy-agent-{self.agent_id[-8:]}"
        
        self.websocket = None
        self.connected = False
        self.wallet = None
        
        # Callback registry for responses
        self.callbacks: Dict[str, Callable] = {}
        
        # Request ID counter
        self.request_id = 0
        
        logger.info(f"Initialized Heavy Agent: {self.agent_id}")
        
    async def connect(self) -> bool:
        """
        Connect to the AgentKit Bridge.
        
        Returns:
            True if connected successfully, False otherwise
        """
        try:
            # Connect to the WebSocket server
            self.websocket = await websockets.connect(self.bridge_url)
            self.connected = True
            
            # Start receiving messages
            asyncio.create_task(self._receive_messages())
            
            # Register the agent
            register_data = {
                "agent_id": self.agent_id,
                "name": self.agent_name
            }
            
            # If wallet_id is provided, include it
            if self.wallet_id:
                register_data["wallet_id"] = self.wallet_id
                
            response = await self.send_request("register_agent", register_data)
            
            if response.get("success"):
                self.wallet_id = response.get("wallet_id")
                self.wallet = response.get("wallet")
                logger.info(f"Agent registered with wallet: {self.wallet_id}")
                return True
            else:
                logger.error(f"Failed to register agent: {response.get('error')}")
                await self.disconnect()
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect to bridge: {str(e)}")
            self.connected = False
            return False
            
    async def disconnect(self) -> None:
        """Disconnect from the AgentKit Bridge."""
        if self.connected and self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.error(f"Error closing WebSocket: {str(e)}")
            finally:
                self.connected = False
                self.websocket = None
                logger.info("Disconnected from bridge")
                
    async def _receive_messages(self) -> None:
        """
        Receive and process messages from the WebSocket.
        """
        if not self.websocket:
            return
            
        try:
            while True:
                # Get message from WebSocket
                message = await self.websocket.recv()
                
                try:
                    # Parse message as JSON
                    data = json.loads(message)
                    
                    # Handle response to a request
                    if "request_id" in data:
                        request_id = data["request_id"]
                        
                        # Call the appropriate callback
                        if request_id in self.callbacks:
                            callback = self.callbacks.pop(request_id)
                            asyncio.create_task(callback(data))
                            
                    # Handle notification
                    elif data.get("type") == "notification":
                        await self._handle_notification(data)
                        
                except json.JSONDecodeError:
                    logger.warning(f"Received non-JSON message: {message}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket connection closed")
            self.connected = False
        except Exception as e:
            logger.error(f"Error receiving messages: {str(e)}")
            self.connected = False
            
    async def _handle_notification(self, data: Dict[str, Any]) -> None:
        """
        Handle notifications from the bridge.
        
        Args:
            data: Notification data
        """
        try:
            # Check if notification is for this agent
            agent_id = data.get("agent_id")
            if agent_id and agent_id != self.agent_id:
                return
                
            event = data.get("event")
            event_data = data.get("data", {})
            
            logger.info(f"Received notification: {event}")
            
            # Handle specific events
            if event == "wallet_updated":
                wallet_id = event_data.get("wallet_id")
                if wallet_id == self.wallet_id:
                    # Update wallet data
                    self.wallet = await self.get_wallet()
                    
            elif event == "transaction_update":
                # Transaction update for this agent's wallet
                tx_data = event_data.get("transaction", {})
                status = tx_data.get("status")
                tx_id = tx_data.get("id")
                logger.info(f"Transaction {tx_id} status: {status}")
                
        except Exception as e:
            logger.error(f"Error handling notification: {str(e)}")
            
    async def send_request(
        self,
        action: str,
        data: Dict[str, Any],
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        """
        Send a request to the bridge and wait for a response.
        
        Args:
            action: Action to perform
            data: Request data
            timeout: Timeout in seconds
            
        Returns:
            Response data
        """
        if not self.connected or not self.websocket:
            raise RuntimeError("Not connected to bridge")
            
        # Create request ID
        self.request_id += 1
        request_id = str(self.request_id)
        
        # Create request message
        request = {
            "action": action,
            "request_id": request_id,
            "data": data
        }
        
        # Create a future for the response
        response_future = asyncio.Future()
        
        # Register callback for this request
        async def callback(response_data: Dict[str, Any]) -> None:
            if not response_future.done():
                response_future.set_result(response_data)
                
        self.callbacks[request_id] = callback
        
        # Send the request
        await self.websocket.send(json.dumps(request))
        
        try:
            # Wait for the response
            response = await asyncio.wait_for(response_future, timeout)
            return response
        except asyncio.TimeoutError:
            # Remove callback if timed out
            if request_id in self.callbacks:
                del self.callbacks[request_id]
            return {"success": False, "error": "Request timed out"}
            
    async def create_wallet(
        self,
        name: Optional[str] = None,
        network: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new wallet.
        
        Args:
            name: Optional wallet name
            network: Optional network
            
        Returns:
            Wallet information or None if failed
        """
        try:
            response = await self.send_request("create_wallet", {
                "name": name,
                "network": network,
                "agent_id": self.agent_id
            })
            
            if response.get("success"):
                wallet = response.get("wallet")
                self.wallet_id = wallet.get("id")
                self.wallet = wallet
                return wallet
            else:
                logger.error(f"Failed to create wallet: {response.get('error')}")
                return None
        except Exception as e:
            logger.error(f"Error creating wallet: {str(e)}")
            return None
            
    async def import_wallet(self, wallet_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Import an existing wallet.
        
        Args:
            wallet_data: Wallet data to import
            
        Returns:
            Imported wallet information or None if failed
        """
        try:
            response = await self.send_request("import_wallet", {
                "wallet": wallet_data,
                "agent_id": self.agent_id
            })
            
            if response.get("success"):
                wallet = response.get("wallet")
                self.wallet_id = wallet.get("id")
                self.wallet = wallet
                return wallet
            else:
                logger.error(f"Failed to import wallet: {response.get('error')}")
                return None
        except Exception as e:
            logger.error(f"Error importing wallet: {str(e)}")
            return None
            
    async def get_wallet(self, wallet_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get wallet information.
        
        Args:
            wallet_id: Optional wallet ID (uses agent's wallet if not provided)
            
        Returns:
            Wallet information or None if failed
        """
        try:
            response = await self.send_request("get_wallet", {
                "wallet_id": wallet_id or self.wallet_id,
                "agent_id": self.agent_id
            })
            
            if response.get("success"):
                return response.get("wallet")
            else:
                logger.error(f"Failed to get wallet: {response.get('error')}")
                return None
        except Exception as e:
            logger.error(f"Error getting wallet: {str(e)}")
            return None
            
    async def list_wallets(self) -> List[Dict[str, Any]]:
        """
        List all wallets.
        
        Returns:
            List of wallet information
        """
        try:
            response = await self.send_request("list_wallets", {})
            
            if response.get("success"):
                return response.get("wallets", [])
            else:
                logger.error(f"Failed to list wallets: {response.get('error')}")
                return []
        except Exception as e:
            logger.error(f"Error listing wallets: {str(e)}")
            return []
            
    async def get_balance(
        self,
        token: str = "eth",
        wallet_id: Optional[str] = None,
        address_id: Optional[str] = None
    ) -> Optional[Decimal]:
        """
        Get wallet balance for a token.
        
        Args:
            token: Token address or symbol
            wallet_id: Optional wallet ID (uses agent's wallet if not provided)
            address_id: Optional specific address to check
            
        Returns:
            Token balance as Decimal or None if failed
        """
        try:
            response = await self.send_request("get_balance", {
                "wallet_id": wallet_id or self.wallet_id,
                "token": token,
                "address_id": address_id,
                "agent_id": self.agent_id
            })
            
            if response.get("success"):
                balance = response.get("balance")
                if balance:
                    return Decimal(balance)
                return Decimal("0")
            else:
                logger.error(f"Failed to get balance: {response.get('error')}")
                return None
        except Exception as e:
            logger.error(f"Error getting balance: {str(e)}")
            return None
            
    async def transfer(
        self,
        amount: Union[str, float, Decimal],
        token: str,
        to_address: str,
        from_address: Optional[str] = None,
        wallet_id: Optional[str] = None,
        gasless: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Transfer tokens from a wallet.
        
        Args:
            amount: Amount to transfer
            token: Token address or symbol
            to_address: Recipient address
            from_address: Optional sender address
            wallet_id: Optional wallet ID (uses agent's wallet if not provided)
            gasless: Whether to use gasless transactions
            
        Returns:
            Transaction information or None if failed
        """
        try:
            response = await self.send_request("transfer", {
                "wallet_id": wallet_id or self.wallet_id,
                "amount": str(amount),
                "token": token,
                "to_address": to_address,
                "from_address": from_address,
                "gasless": gasless,
                "agent_id": self.agent_id
            })
            
            if response.get("success"):
                return response.get("transaction")
            else:
                logger.error(f"Failed to transfer: {response.get('error')}")
                return None
        except Exception as e:
            logger.error(f"Error transferring tokens: {str(e)}")
            return None
            
    async def trade(
        self,
        amount: Union[str, float, Decimal],
        from_token: str,
        to_token: str,
        wallet_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Trade tokens.
        
        Args:
            amount: Amount to trade
            from_token: Source token address or symbol
            to_token: Destination token address or symbol
            wallet_id: Optional wallet ID (uses agent's wallet if not provided)
            
        Returns:
            Transaction information or None if failed
        """
        try:
            response = await self.send_request("trade", {
                "wallet_id": wallet_id or self.wallet_id,
                "amount": str(amount),
                "from_token": from_token,
                "to_token": to_token,
                "agent_id": self.agent_id
            })
            
            if response.get("success"):
                return response.get("transaction")
            else:
                logger.error(f"Failed to trade: {response.get('error')}")
                return None
        except Exception as e:
            logger.error(f"Error trading tokens: {str(e)}")
            return None

async def main() -> None:
    """
    Main entry point for the ElizaOS Agent.
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Heavy ElizaOS Agent")
    parser.add_argument("--bridge-url", default="ws://localhost:8765", help="AgentKit Bridge WebSocket URL")
    parser.add_argument("--agent-id", help="Agent ID (generated if not provided)")
    parser.add_argument("--wallet-id", help="Wallet ID to associate with this agent")
    parser.add_argument("--agent-name", help="Agent name")
    args = parser.parse_args()
    
    # Create the Heavy Agent
    agent = HeavyAgent(
        bridge_url=args.bridge_url,
        agent_id=args.agent_id,
        wallet_id=args.wallet_id,
        agent_name=args.agent_name
    )
    
    try:
        # Connect to the bridge
        logger.info(f"Connecting to bridge at {args.bridge_url}...")
        connected = await agent.connect()
        
        if not connected:
            logger.error("Failed to connect to bridge")
            return
            
        logger.info(f"Connected to bridge with agent ID: {agent.agent_id}")
        
        # Get wallet information
        wallet = await agent.get_wallet()
        if wallet:
            logger.info(f"Using wallet: {wallet.get('id')} ({wallet.get('name')})")
        
        # Example: Get ETH balance
        balance = await agent.get_balance("eth")
        if balance is not None:
            logger.info(f"ETH Balance: {balance}")
            
        # Keep the agent running
        logger.info("Agent is running. Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(60)
            
    except KeyboardInterrupt:
        logger.info("Stopping agent...")
    except Exception as e:
        logger.error(f"Error in agent: {str(e)}")
    finally:
        # Disconnect from the bridge
        await agent.disconnect()
        logger.info("Agent stopped.")

if __name__ == "__main__":
    asyncio.run(main()) 