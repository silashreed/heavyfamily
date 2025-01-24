# src/heavy/agents/risk_agent.py

import os
import aiohttp
import asyncio
import logging

# Load environment variables so we can securely fetch API keys from .env
from dotenv import load_dotenv
load_dotenv()

from heavy.concurrency_throttler import ConcurrencyThrottler
from heavy.core.config_tools import load_aggregator_hpc_config

logger = logging.getLogger(__name__)

# Securely load all scan & aggregator API keys
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
ARBISCAN_API_KEY = os.getenv("ARBISCAN_API_KEY", "")
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
COINMARKETCAP_API_KEY = os.getenv("COINMARKETCAP_API_KEY", "")
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY", "")

# Aggregator key for holder distribution
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")

# 2) Load risk thresholds from aggregator_hpc_config.json
RISK_CFG = load_aggregator_hpc_config("src/heavy/aggregator_hpc_config.json")
MIN_LIQUIDITY_USD = float(RISK_CFG.get("minLiquidityUsd", 50000.0))
MIN_VOLUME_24H_USD = float(RISK_CFG.get("minVolume24hUsd", 20000.0))
MIN_UNIQUE_HOLDERS = int(RISK_CFG.get("minUniqueHolders", 500))
MAX_TOP_HOLDERS_PERCENT = float(RISK_CFG.get("maxTopHoldersPercent", 50.0))

# 3) Constants for DexScreener, Etherscan, Covalent endpoints
DEXSCREENER_API_URL = "https://api.dexscreener.com/latest/dex/tokens"
ETHERSCAN_BASE_URL = "https://api.etherscan.io/api"

MORALIS_BASE_URL = "https://deep-index.moralis.io/api/v2"

class RiskAgent:
    """
    The RiskAgent is responsible for evaluating potential risks in synergy strategies
    and newly discovered tokens. It uses external data (DexScreener, Etherscan, Covalent)
    to gauge liquidity, volume, and holder distribution.
    """

    async def assess_token(self, token_address: str, concurrency: ConcurrencyThrottler = None) -> bool:
        """
        Asynchronously assess a newly discovered token's risk by checking:
          1) Liquidity (via _check_liquidity)
          2) Volume (via _check_volume)
          3) Holder distribution (via _check_holder_distribution)

        :param token_address: The ERC20 token address (0x...) to assess.
        :param concurrency:  Optional ConcurrencyThrottler instance for rate-limit external API calls.
        :return: True if the token passes all checks, False otherwise.
        """
        logger.info(f"[RiskAgent] assess_token => start => {token_address}")

        # If no concurrency is passed, create a local throttler to avoid hitting rate limits
        if concurrency is None:
            concurrency = ConcurrencyThrottler(
                max_requests_per_minute=float(RISK_CFG.get("maxRequestsPerMinute", 300)),
                max_tokens_per_minute=float(RISK_CFG.get("maxTokensPerMinute", 300)),
                rate_limit_backoff_seconds=float(RISK_CFG.get("rateLimitBackoffSeconds", 5.0))
            )

        try:
            # Run checks concurrently for performance
            results = await asyncio.gather(
                self._check_liquidity(token_address, concurrency),
                self._check_volume(token_address, concurrency),
                self._check_holder_distribution(token_address, concurrency)
            )
            # If any fails => token is not safe
            if not all(results):
                logger.warning(f"[RiskAgent] assess_token => token={token_address} => FAIL => {results}")
                return False

            logger.info(f"[RiskAgent] assess_token => token={token_address} => PASSED => all checks")
            return True
        except Exception as e:
            logger.error(f"[RiskAgent] assess_token => error => token={token_address}, {e}")
            return False

    async def _check_liquidity(self, token_address: str, concurrency: ConcurrencyThrottler) -> bool:
        """
        Checks if the token has sufficient liquidity on reputable DEXs (e.g., Uniswap, SushiSwap).
        Uses DexScreener's API as the primary data source.

        :param token_address: The token address to check (0x...).
        :param concurrency:   A ConcurrencyThrottler for rate limiting.
        :return: True if liquidity >= MIN_LIQUIDITY_USD, False otherwise or on error.
        """
        try:
            url = f"{DEXSCREENER_API_URL}/{token_address.lower()}"
            await concurrency.acquire(num_tokens=30)  # tokens required for the request

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as resp:
                    if resp.status == 429:
                        concurrency.note_rate_limit_error()
                        logger.warning("[RiskAgent] _check_liquidity => 429 Too Many Requests (DexScreener)")
                        return False
                    resp.raise_for_status()
                    data = await resp.json()

            pairs = data.get("pairs", [])
            max_liq = 0.0
            for p in pairs:
                liq = float(p.get("liquidity", {}).get("usd", 0.0))
                if liq > max_liq:
                    max_liq = liq

            if max_liq >= MIN_LIQUIDITY_USD:
                logger.info(f"[RiskAgent] liquidity check => {token_address}, max_liq={max_liq:.2f} => PASS")
                return True
            else:
                logger.warning(f"[RiskAgent] liquidity check => {token_address}, max_liq={max_liq:.2f} < threshold={MIN_LIQUIDITY_USD}")
                return False

        except Exception as e:
            logger.error(f"[RiskAgent] _check_liquidity => error => {e}")
            return False

    async def _check_volume(self, token_address: str, concurrency: ConcurrencyThrottler) -> bool:
        """
        Checks if the token has sufficient 24-hour trading volume. Uses DexScreener's
        aggregated data. Threshold=MIN_VOLUME_24H_USD.

        :param token_address: The token address (0x...).
        :param concurrency:   A ConcurrencyThrottler for rate limiting.
        :return: True if 24h volume >= MIN_VOLUME_24H_USD, False otherwise or on error.
        """
        try:
            url = f"{DEXSCREENER_API_URL}/{token_address.lower()}"
            await concurrency.acquire(num_tokens=30)

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as resp:
                    if resp.status == 429:
                        concurrency.note_rate_limit_error()
                        logger.warning("[RiskAgent] _check_volume => 429 Too Many Requests (DexScreener)")
                        return False
                    resp.raise_for_status()
                    data = await resp.json()

            pairs = data.get("pairs", [])
            highest_24h_vol = 0.0
            for p in pairs:
                vol_24h = float(p.get("volume", {}).get("h24", 0.0))
                if vol_24h > highest_24h_vol:
                    highest_24h_vol = vol_24h

            if highest_24h_vol >= MIN_VOLUME_24H_USD:
                logger.info(f"[RiskAgent] volume check => {token_address}, volume24h={highest_24h_vol:.2f} => PASS")
                return True
            else:
                logger.warning(f"[RiskAgent] volume check => {token_address}, volume24h={highest_24h_vol:.2f} < threshold={MIN_VOLUME_24H_USD}")
                return False

        except Exception as e:
            logger.error(f"[RiskAgent] _check_volume => error => {e}")
            return False

    async def _check_holder_distribution(self, token_address: str, concurrency: ConcurrencyThrottler) -> bool:
        """
        Checks the holder distribution to identify potential whales or concentration risk.
        1) Fetches the total token supply from Etherscan (for consistency).
        2) Fetches total holders count + top 10 holder distribution from Covalent.
        3) Ensures holders >= MIN_UNIQUE_HOLDERS & top-10 holders <= MAX_TOP_HOLDERS_PERCENT.

        :param token_address: The token address (0x...).
        :param concurrency:   A ConcurrencyThrottler for rate limiting.
        :return: True if distribution is acceptable, False on error or if thresholds fail.
        """
        try:
            # Step 1) get total supply from Etherscan
            supply_url = (
                f"{ETHERSCAN_BASE_URL}?module=stats&action=tokensupply&contractaddress={token_address}"
                f"&apikey={ETHERSCAN_API_KEY}"
            )
            await concurrency.acquire(num_tokens=10)

            total_supply = 0.0
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(supply_url) as resp:
                    if resp.status == 429:
                        concurrency.note_rate_limit_error()
                        logger.warning("[RiskAgent] _check_holder_distribution => 429 Etherscan => supply")
                        return False
                    resp.raise_for_status()
                    data = await resp.json()
                    supply_str = data.get("result","0")
                    total_supply = float(supply_str)

            if total_supply <= 0:
                logger.warning(f"[RiskAgent] holder_distribution => {token_address}, totalSupply=0 => skip")
                return False

            # Steps 2 & 3) holders count + top10 distribution from Covalent
            holders_count = await self._fetch_holders_count_etherscan(token_address, concurrency)
            if holders_count < MIN_UNIQUE_HOLDERS:
                logger.warning(f"[RiskAgent] holder_distribution => {token_address}, holders={holders_count} < {MIN_UNIQUE_HOLDERS}")
                return False

            top_10_percent = await self._fetch_top_10_percent(token_address, total_supply, concurrency)
            if top_10_percent > MAX_TOP_HOLDERS_PERCENT:
                logger.warning(f"[RiskAgent] holder_distribution => {token_address}, top10%={top_10_percent:.2f}% > {MAX_TOP_HOLDERS_PERCENT}")
                return False

            logger.info(f"[RiskAgent] holder_distribution => token={token_address}, holders={holders_count}, top10%={top_10_percent:.2f}% => PASS")
            return True

        except Exception as e:
            logger.error(f"[RiskAgent] _check_holder_distribution => error => token={token_address}, {e}")
            return False

    async def _fetch_holders_count_etherscan(self, token_address: str, concurrency: ConcurrencyThrottler) -> int:
        """
        Retrieves total unique holders from Moralis for the token (ETH mainnet).
        Endpoint:
        GET /erc20/{address}/holders?chain=0x1&limit=1
        We'll parse the 'total' field for the total # of holders.
        Return 0 if an error occurs.

        :param token_address: The ERC20 token address (0x...).
        :param concurrency:   ConcurrencyThrottler for rate limiting.
        :return: The total unique holders count, or 0 on error.
        """
        if not MORALIS_API_KEY:
            logger.warning("[RiskAgent] _fetch_holders_count_etherscan => no Moralis API key => fallback=0")
            return 0

        # Using chain=0x1 for Ethereum mainnet. Adjust chain param as needed.
        url = f"{MORALIS_BASE_URL}/erc20/{token_address.lower()}/holders?chain=0x1&limit=1"
        headers = {"X-API-Key": MORALIS_API_KEY}

        try:
            await concurrency.acquire(num_tokens=10)

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 429:
                        concurrency.note_rate_limit_error()
                        logger.warning("[RiskAgent] _fetch_holders_count_etherscan => 429 Moralis => holders_count")
                        return 0
                    resp.raise_for_status()
                    data = await resp.json()

            # Moralis response typically has "total", "page", "page_size", "cursor", "result": [...]
            total = data.get("total", 0)
            logger.info(f"[RiskAgent] holders_count => token={token_address}, count={total}")
            return int(total)
        except Exception as e:
            logger.error(f"[RiskAgent] _fetch_holders_count_etherscan => error => {e}")
            return 0

async def _fetch_top_10_percent(self, token_address: str, total_supply: float, concurrency: ConcurrencyThrottler) -> float:
    """
    Retrieves top holders from Moralis, sorts them by balance, sums the top 10,
    then calculates the % of total_supply.

    :param token_address: The ERC20 token address (0x...).
    :param total_supply:  The token's total supply (units).
    :param concurrency:   ConcurrencyThrottler for rate limiting.
    :return: The % of tokens owned by top 10 holders, e.g. 40.0 => 40%.
    """
    if not MORALIS_API_KEY:
        logger.warning("[RiskAgent] _fetch_top_10_percent => no Moralis API key => fallback=100.0")
        return 100.0

    # For top holders, set limit=100 or enough to ensure top 10 are retrieved
    url = f"{MORALIS_BASE_URL}/erc20/{token_address.lower()}/holders?chain=0x1&limit=100"
    headers = {"X-API-Key": MORALIS_API_KEY}

    try:
        await concurrency.acquire(num_tokens=10)

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 429:
                    concurrency.note_rate_limit_error()
                    logger.warning("[RiskAgent] _fetch_top_10_percent => 429 Moralis => holders")
                    return 100.0
                resp.raise_for_status()
                data = await resp.json()

        # Example Moralis response structure:
        # {
        #   "total": 12345,
        #   "page": 0,
        #   "page_size": 100,
        #   "cursor": null,
        #   "result": [
        #       {"holder_address": "...", "holder_balance": "12345", ...}, ...
        #   ]
        # }
        items = data.get("result", [])
        if not items:
            logger.warning(f"[RiskAgent] top_10 => token={token_address}, no holder data => fallback=100%")
            return 100.0

        # Sort by descending holder_balance
        sorted_holders = sorted(
            items, key=lambda i: float(i.get("holder_balance", "0")), reverse=True
        )
        top_10 = sorted_holders[:10]

        sum_top_10 = 0.0
        for holder in top_10:
            bal_str = holder.get("holder_balance", "0")
            sum_top_10 += float(bal_str)

        if total_supply <= 0:
            logger.warning(f"[RiskAgent] top_10 => token={token_address}, total_supply=0 => fallback=100%")
            return 100.0

        percent_top_10 = (sum_top_10 / total_supply) * 100.0
        logger.info(f"[RiskAgent] top_10 => token={token_address}, sum={sum_top_10:.2f}, supply={total_supply:.2f}, %={percent_top_10:.2f}")
        return percent_top_10

    except Exception as e:
        logger.error(f"[RiskAgent] _fetch_top_10_percent => error => {e}")
        return 100.0