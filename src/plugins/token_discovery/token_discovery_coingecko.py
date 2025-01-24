import os
import json
import aiohttp
import asyncio
import logging
from typing import Optional, Dict, Any
from heavy.logger import log_event
from heavy.concurrency_throttler import ConcurrencyThrottler

logger = logging.getLogger(__name__)

TOKEN_REGISTRY_FILE = os.path.join(
    os.path.dirname(__file__), "token_registry.json"
)
COINGECKO_API_URL = "https://api.coingecko.com/api/v3"

def load_token_registry() -> Dict[str, str]:
    """Loads the local token registry (symbol/name to address mappings)."""
    if not os.path.isfile(TOKEN_REGISTRY_FILE):
        return {}
    try:
        with open(TOKEN_REGISTRY_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Error loading token registry: {e}")
        return {}

def save_token_registry(reg: Dict[str, str]) -> None:
    """Writes the updated token registry to the JSON file."""
    try:
        with open(TOKEN_REGISTRY_FILE, "w") as f:
            json.dump(reg, f, indent=2)
        logger.info(f"Updated token registry: {TOKEN_REGISTRY_FILE}")
    except Exception as e:
        logger.error(f"Error saving token registry: {e}")

async def discover_token_on_coingecko_async(
    query: str,
    concurrency: Optional[ConcurrencyThrottler] = None
) -> Optional[str]:
    """
    Asynchronously fetches the Ethereum contract address for a token from CoinGecko.

    :param query: Token name or symbol (e.g., "USDC", "DAI").
    :param concurrency: Optional ConcurrencyThrottler instance for rate limiting.
    :return: Ethereum contract address (0x...) if found, else None.
    """
    reg = load_token_registry()
    q_lower = query.lower()
    if q_lower in reg:
        return reg[q_lower]

    all_coins_url = f"{COINGECKO_API_URL}/coins/list"
    all_coins = []

    try:
        if concurrency:
            await concurrency.acquire(num_tokens=50)

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(all_coins_url) as resp:
                if resp.status == 429:
                    if concurrency:
                        concurrency.note_rate_limit_error()
                    logger.warning("CoinGecko API rate limit exceeded (429 error).")
                    return None
                resp.raise_for_status()
                all_coins = await resp.json()
    except Exception as e:
        logger.error(f"Error fetching coin list from CoinGecko: {e}")
        return None

    matched_id = next((c.get("id", "") for c in all_coins if c.get("symbol", "").lower() == q_lower or c.get("name", "").lower() == q_lower), None)

    if not matched_id:
        logger.info(f"No coin found matching: {query}")
        return None

    coin_url = f"{COINGECKO_API_URL}/coins/{matched_id}"

    try:
        if concurrency:
            await concurrency.acquire(num_tokens=20)

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(coin_url) as c_resp:
                if c_resp.status == 429:
                    if concurrency:
                        concurrency.note_rate_limit_error()
                    logger.warning("CoinGecko API rate limit exceeded (429 error).")
                    return None
                c_resp.raise_for_status()
                coin_data = await c_resp.json()
    except Exception as e:
        logger.error(f"Error fetching coin details for {matched_id}: {e}")
        return None

    eth_addr = coin_data.get("platforms", {}).get("ethereum", "").lower().strip()

    if not eth_addr or not eth_addr.startswith("0x") or len(eth_addr) < 42:
        logger.warning(f"Invalid or missing Ethereum address for {matched_id}.")
        return None

    reg[q_lower] = eth_addr
    save_token_registry(reg)
    logger.info(f"Discovered and cached token: {query} => {eth_addr}")
    return eth_addr

def get_token_from_registry(query: str) -> Optional[str]:
    """
    Looks up a token in the local registry.

    :param query: Token symbol or name (e.g., "USDC").
    :return: 0x address if found, else None.
    """
    return load_token_registry().get(query.lower())