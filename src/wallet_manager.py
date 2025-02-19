# src/heavy/core/wallet_manager.py

"""
WalletManager for self-custody or partial custody approaches.
Includes asynchronous functionality for bridging, trading, and general on-chain interactions.
"""

import os
import logging
import time
import asyncio
from typing import Optional, Dict, Any, Tuple, List

import aiohttp
from web3 import Web3, HTTPProvider, WebSocketProvider
from web3.exceptions import ContractLogicError, TransactionNotFound, TimeExhausted
from eth_account.datastructures import SignedTransaction
from eth_account import Account

from heavy.logger import log_event
from heavy.core.config_tools import load_wallet_manager_config

# Ephemeral memory tools (if available)
try:
    from heavy.core.ephemeral_memory import read_agent_state_tool
    MEMORY_TOOLS_AVAILABLE = True
except ImportError:
    MEMORY_TOOLS_AVAILABLE = False

logger = logging.getLogger(__name__)

# Load configuration
WALLET_CFG = load_wallet_manager_config()

# Constants
GAS_BUFFER_MULTIPLIER = float(WALLET_CFG.get("gasBufferMultiplier", 1.2))
BRIDGE_TX_DEADLINE_SEC = int(WALLET_CFG.get("bridgeTxDeadlineSec", 3600))
MIN_PRIORITY_FEE_GWEI = float(WALLET_CFG.get("minPriorityFeeGwei", 2))
PRIORITY_FEE_MULTIPLIER = float(WALLET_CFG.get("priorityFeeMultiplier", 1.2))
HIGH_BASE_FEE_THRESHOLD_GWEI = float(WALLET_CFG.get("highBaseFeeThresholdGwei", 100))

# Token prices caching
_PRICE_CACHE: Dict[str, Tuple[float, float]] = {}
PRICE_CACHE_TTL = 300.0  # 5 minutes

class WalletManager:

    def __init__(self):
        # Load private key from environment variable
        self.private_key = os.getenv("PRIVATE_KEY") or ""
        if not self.private_key:
            log_event("[WalletManager] No private key set. Operating in read-only mode.", "WARNING")

        self.default_network_id = WALLET_CFG.get("default_network_id", "base-mainnet")
        self.web3_map: Dict[str, Web3] = {}
        self.local_nonce: Dict[str, int] = {}

        # Initialize Web3 providers asynchronously
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._init_providers())
        except RuntimeError:
            asyncio.run(self._init_providers())

    async def _init_providers(self):
        """
        Asynchronously initializes Web3 providers for various chains.
        """
        for chain_id, rpc_urls in WALLET_CFG.get("chain_rpc_map", {}).items():
            for url in rpc_urls:
                try:
                    if url.startswith("ws"):
                        provider = WebSocketProvider(url)
                    else:
                        provider = HTTPProvider(url)
                    web3 = Web3(provider)
                    if web3.is_connected():
                        self.web3_map[chain_id.lower()] = web3
                        log_event(f"[WalletManager] Connected to {chain_id} at {url}", "INFO")
                        break
                    else:
                        log_event(f"[WalletManager] Failed to connect to {chain_id} at {url}", "WARNING")
                except Exception as e:
                    log_event(f"[WalletManager] Error connecting to {chain_id} at {url}: {e}", "ERROR")

    @property
    def address(self) -> Optional[str]:
        """
        Returns the wallet address associated with the private key.
        """
        if not self.private_key:
            return None
        return Account.from_key(self.private_key).address

    def _normalize_chain_identifier(self, chain_identifier: str) -> str:
        """
        Normalizes chain identifiers to a standard format.
        """
        chain_identifier = chain_identifier.strip().lower()
        if chain_identifier == "ethereum":
            return "ethereum-mainnet"
        return chain_identifier

    async def get_web3_for_chain(self, chain_identifier: str) -> Optional[Web3]:
        """
        Returns a Web3 instance for the specified chain.
        """
        chain_identifier = self._normalize_chain_identifier(chain_identifier)
        return self.web3_map.get(chain_identifier)

    async def _get_nonce(self, chain_identifier: str) -> int:
        """
        Retrieves the current transaction nonce for the specified chain.
        """
        web3 = await self.get_web3_for_chain(chain_identifier)
        if not web3:
            raise ValueError(f"No Web3 provider for chain: {chain_identifier}")
        return web3.eth.get_transaction_count(self.address, "pending")

    async def sign_transaction_async(self, tx_data: Dict[str, Any], chain_identifier: str) -> SignedTransaction:
        """
        Signs a transaction asynchronously.
        """
        if not self.private_key:
            raise ValueError("Cannot sign transaction without a private key.")
        tx_data["nonce"] = await self._get_nonce(chain_identifier)
        return await asyncio.to_thread(Account.from_key(self.private_key).sign_transaction, tx_data)

    async def sign_message_async(self, message_bytes: bytes) -> bytes:
        """
        Signs a message asynchronously.
        """
        if not self.private_key:
            raise ValueError("Cannot sign message without a private key.")
        signature = await asyncio.to_thread(Account.from_key(self.private_key).sign_message, message_bytes)
        return signature.signature

    async def send_transaction_async(self, signed_tx: SignedTransaction, chain_identifier: str) -> Optional[str]:
        """
        Sends a signed transaction asynchronously.
        """
        web3 = await self.get_web3_for_chain(chain_identifier)
        if not web3:
            raise ValueError(f"No Web3 provider for chain: {chain_identifier}")
        try:
            tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            log_event(f"[WalletManager] Transaction sent: {tx_hash.hex()}", "INFO")
            return tx_hash.hex()
        except Exception as e:
            log_event(f"[WalletManager] Error sending transaction: {e}", "ERROR")
            return None

    async def _get_token_price_usd(self, token_symbol: str) -> float:
        """
        Retrieves the USD price of a token using CoinGecko.
        """
        if not token_symbol:
            return 0.0
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={token_symbol}&vs_currencies=usd"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return data.get(token_symbol, {}).get("usd", 0.0)
        except Exception as e:
            logger.error(f"[WalletManager] Error fetching token price: {e}")
            return 0.0