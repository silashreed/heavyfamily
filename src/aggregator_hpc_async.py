import os
import json
import math
import time
import asyncio
import aiohttp
import logging
import traceback
from typing import List, Dict, Any, Optional

from heavy.logger import log_event
from heavy.core.config_tools import load_aggregator_hpc_config
from heavy.core.positions_manager import PositionsManager
from heavy.core.wallet_manager import WalletManager
from heavy.concurrency_throttler import ConcurrencyThrottler
from heavy.historical_data_async import (
    fetch_pool_history_async,
    compute_volatility_from_history_async,
)

logger = logging.getLogger(__name__)

###############################################################################
# 1) Load aggregator HPC synergy config => aggregator_hpc_config.json
###############################################################################
AGGREGATOR_CFG = load_aggregator_hpc_config("src/heavy/aggregator_hpc_config.json")

# LSD merges toggle
LSD_MERGE_ENABLED = bool(AGGREGATOR_CFG.get("lsdMerges", True))

# Additional config-driven constants
FALLBACK_VOL = float(AGGREGATOR_CFG.get("fallbackVol", 0.05))
MAX_FETCH_RETRIES = int(AGGREGATOR_CFG.get("maxFetchRetries", 3))
MAX_POOLS = int(AGGREGATOR_CFG.get("maxPools", 50))
BORDERLINE_SYNERGY_THRESHOLD = float(
    AGGREGATOR_CFG.get("borderlineSynergyThreshold", 2.0)
)

# For bridging synergy integration
BRIDGING_ENABLED = bool(AGGREGATOR_CFG.get("enableBridging", False))

# Example bridging synergy defaults (override in aggregator_hpc_config.json if desired)
DEFAULT_BASE_APR = float(AGGREGATOR_CFG.get("baseApr", 3.0))
DEFAULT_TARGET_APR = float(AGGREGATOR_CFG.get("targetApr", 7.0))
DEFAULT_HOLDING_DAYS = float(AGGREGATOR_CFG.get("holdingDays", 30.0))
DEFAULT_RISK_THRESHOLD = float(AGGREGATOR_CFG.get("riskThreshold", 5.0))
PAPER_MODE = bool(AGGREGATOR_CFG.get("paperTradeMode", True))

# Default file paths (configurable if desired)
DEFAULT_CACHE_FILE = "pools_data.json"
DEFAULT_OUTPUT_FILE = "aggregator_output.json"

positions_manager = PositionsManager()
wallet_manager = WalletManager()

###############################################################################
# 2) LSD tokens or detection logic
###############################################################################
LSD_TOKENS = {"steth", "reth", "stmatic", "seth2", "ankr"}


def is_lsd_symbol(symbol: str) -> bool:
    """
    Check if a token symbol corresponds to a known LSD (Liquid Staking Derivative).

    :param symbol: Token symbol (e.g. 'steth').
    :return: True if symbol is recognized as LSD, False otherwise.
    """
    return symbol.lower() in LSD_TOKENS


###############################################################################
# 3) Async DeFiLlama fetch with concurrency throttling
###############################################################################
DEFAULT_DEFI_LLAMA_URL = "https://yields.llama.fi/pools"
CACHE_MAX_AGE = 600  # 10 minutes


async def fetch_defillama_pools_async(
    concurrency: Optional[ConcurrencyThrottler] = None,
    cache_file: str = DEFAULT_CACHE_FILE,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """
    Asynchronously fetch pool data from DeFiLlama. Respects concurrency throttle.
    Caches the results for up to CACHE_MAX_AGE seconds if 'use_cache' is True.

    :param concurrency: An optional ConcurrencyThrottler instance for controlling
                        outbound request rates.
    :param cache_file:  Local JSON file used for caching DeFiLlama pool data.
    :param use_cache:   If True, will check and potentially use the local cache.
    :return: A list of pool objects from DeFiLlama (each is a dict with fields
             like 'pool', 'chain', 'apy', 'tvlUsd', etc.).
    """
    url = os.getenv("DEFI_LLAMA_API_URL", DEFAULT_DEFI_LLAMA_URL)

    # 1) Check local cache
    if use_cache and os.path.exists(cache_file):
        age = time.time() - os.path.getmtime(cache_file)
        if age < CACHE_MAX_AGE:
            try:
                with open(cache_file, "r") as cf:
                    data = json.load(cf)
                if isinstance(data, list):
                    msg = (
                        f"[fetch_defillama_pools_async] Using cached DeFiLlama data => "
                        f"age={age:.1f}s, file={cache_file}"
                    )
                    log_event(msg, "INFO")
                    return data
            except Exception as e:
                log_event(
                    f"[fetch_defillama_pools_async] Error reading cache => {e}",
                    "WARNING",
                )

    raw_pools = []
    attempt = 1
    backoff = 2

    # 2) Fetch from DeFiLlama with retries
    while attempt <= MAX_FETCH_RETRIES:
        try:
            if concurrency:
                # Acquire tokens for the fetch if concurrency limiting is used
                await concurrency.acquire(num_tokens=50)

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.get(url) as resp:
                    if resp.status == 429:
                        if concurrency:
                            concurrency.note_rate_limit_error()
                        log_event(
                            f"[fetch_defillama_pools_async] 429 => attempt={attempt}, backoff={backoff}s",
                            "WARNING",
                        )
                        await asyncio.sleep(backoff)
                        if attempt < MAX_FETCH_RETRIES:
                            backoff = min(backoff * 2, 30)
                        attempt += 1
                        continue

                    resp.raise_for_status()
                    data = await resp.json()
                    if "data" in data and isinstance(data["data"], list):
                        raw_pools = data["data"]
                    elif isinstance(data, list):
                        raw_pools = data
                    else:
                        raise ValueError(
                            "[fetch_defillama_pools_async] invalid structure => no 'data' key."
                        )

            # 3) Cache results
            try:
                with open(cache_file, "w") as cf:
                    json.dump(raw_pools, cf, indent=2)
                log_event(
                    f"[fetch_defillama_pools_async] Cached => {cache_file}", "INFO"
                )
            except Exception as e:
                log_event(
                    f"[fetch_defillama_pools_async] Error caching => {e}", "ERROR"
                )

            msg = f"[fetch_defillama_pools_async] got {len(raw_pools)} pools, attempt={attempt}"
            log_event(msg, "INFO")
            return raw_pools

        except Exception as e:
            tb_str = traceback.format_exc()
            log_event(
                f"[fetch_defillama_pools_async] attempt={attempt}, error={e}\n{tb_str}",
                "WARNING",
            )
            if attempt < MAX_FETCH_RETRIES:
                log_event(
                    f"[fetch_defillama_pools_async] sleeping {backoff}s => next attempt",
                    "INFO",
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            attempt += 1

    log_event("[fetch_defillama_pools_async] all attempts => empty list", "ERROR")
    return raw_pools


###############################################################################
# 4) LSD synergy => asynchronous subgraph fetch
###############################################################################
async def discover_lsd_subgraphs_async(
    concurrency: Optional[ConcurrencyThrottler] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch LSD (Liquid Staking Derivative) data from The Graph or other endpoints, asynchronously.
    In production, you can expand to multiple LSD providers (Lido, Rocket Pool, etc.) via asyncio.gather.

    :param concurrency: Optionally pass a ConcurrencyThrottler if you want to limit concurrency.
    :return: A list of LSD pool objects, each containing fields like:
             {
                "pool_id": "lsd-steth",
                "chain": "ethereum",
                "symbol": "steth",
                "apy": <float>,
                "tvl": <float>,
                "volatility": <float>,
                "is_lsd": True
             }
    """
    if not LSD_MERGE_ENABLED:
        return []

    lido_subgraph_url = os.getenv(
        "LIDO_SUBGRAPH_URL", "https://api.thegraph.com/subgraphs/name/lidofinance/lido"
    )
    query = """
    query {
      totals(first:1, orderBy:block, orderDirection:desc) {
        tvl
        apr
      }
    }
    """

    # Acquire concurrency tokens if provided
    if concurrency:
        await concurrency.acquire(num_tokens=10)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        try:
            async with session.post(lido_subgraph_url, json={"query": query}) as resp:
                resp.raise_for_status()
                data = await resp.json()
                totals = data.get("data", {}).get("totals", [])
                if not totals:
                    return []

                tvl = float(totals[0].get("tvl", 0.0))
                # Subgraph often provides APR in decimal form (e.g. 0.06 => 6%)
                apr = float(totals[0].get("apr", 0.0)) * 100.0

                return [
                    {
                        "pool_id": "lsd-steth",
                        "chain": "ethereum",
                        "symbol": "steth",
                        "apy": apr,
                        "tvl": tvl,
                        "volatility": FALLBACK_VOL,
                        "is_lsd": True,
                    }
                ]

        except Exception as e:
            tb_str = traceback.format_exc()
            msg = f"[discover_lsd_subgraphs_async] error => {e}\n{tb_str}"
            log_event(msg, "WARNING")
            return []


###############################################################################
# 5) Parse, augment, and rank DeFiLlama data
###############################################################################
def parse_defillama_data(raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert raw DeFiLlama pool objects into a standardized synergy list,
    adding default HPC aggregator fields.

    :param raw_list: List of raw pool dicts from DeFiLlama.
    :return: A list of synergy pool objects with standardized fields.
    """
    synergy_list = []
    for i, d in enumerate(raw_list):
        pool_id = d.get("pool", f"defi-{i}")
        chain = str(d.get("chain", "ethereum")).lower()
        if chain == "eth":
            chain = "ethereum"

        synergy_list.append({
            "pool_id": pool_id,
            "chain": chain,
            "symbol": str(d.get("symbol", d.get("project", ""))).lower(),
            "apy": float(d.get("apy", 0.0)),
            "tvl": float(d.get("tvlUsd", 0.0)),
            "volatility": FALLBACK_VOL,
            "is_lsd": False
        })
    return synergy_list


def augment_pool_features(pool: Dict[str, Any]) -> None:
    """
    Add additional metadata or computed fields to a synergy pool object:
      1) bridging_needed => True if chain != 'ethereum'
      2) chain_factor => retrieved from bridging_data or watchers
      3) LSD detection => if symbol is recognized as LSD
      4) apy_vol_ratio => apy / volatility

    :param pool: A synergy pool dict with 'chain', 'symbol', 'apy', 'volatility'.
    """
    chain_str = pool["chain"]
    bridging_needed = (chain_str != "ethereum")
    pool["bridging_needed"] = bridging_needed

    # Retrieve chain risk factor
    from heavy.plugins.bridging.bridging_data import get_chain_risk_factor
    cf = get_chain_risk_factor(chain_str) or 1.0
    pool["chain_factor"] = cf

    # LSD detection if not already flagged
    if is_lsd_symbol(pool["symbol"]):
        pool["is_lsd"] = True

    # Compute ratio
    apy_val = pool["apy"]
    vol_val = pool["volatility"]
    ratio = apy_val / vol_val if vol_val > 1e-9 else 999.0
    pool["apy_vol_ratio"] = ratio


def prune_pool(pool: Dict[str, Any]) -> bool:
    """
    Decide whether to keep or discard a synergy pool based on aggregator thresholds
    from aggregator_hpc_config.json.

    :param pool: A synergy pool dict. Must contain 'apy', 'tvl', 'chain_factor'.
    :return: True if the pool meets thresholds, otherwise False.
    """
    min_apy = float(AGGREGATOR_CFG.get("minApy", 1.0))
    min_tvl = float(AGGREGATOR_CFG.get("minTvl", 1_000_000.0))
    max_cf = float(AGGREGATOR_CFG.get("maxChainFactor", 2.0))

    if pool["apy"] < min_apy:
        return False
    if pool["tvl"] < min_tvl:
        return False
    if pool["chain_factor"] > max_cf:
        return False
    return True


def final_score_formula(pool: Dict[str, Any]) -> float:
    """
    Compute the final aggregator HPC synergy score for a pool.

    :param pool: Synergy pool.
    :return:     The pool's score.
    """
    aggregator_weights = AGGREGATOR_CFG.get("aggregatorWeights", {})
    alpha = float(aggregator_weights.get("alpha", 10.0))
    beta = float(aggregator_weights.get("beta", 3.0))
    gamma = float(aggregator_weights.get("gamma", 0.8))
    delta = float(aggregator_weights.get("delta", 0.02))
    bridging_penalty = float(aggregator_weights.get("bridgingPenalty", 0.0))
    lsd_penalty = float(aggregator_weights.get("lsdBridgingMultiplierPenalty", 0.0))

    chain_factor = float(pool.get("chain_factor", 1.0))
    bridging_needed = bool(pool.get("bridging_needed", False))
    is_lsd = bool(pool.get("is_lsd", False))

    apy = float(pool.get("apy", 0.0))
    vol = float(pool.get("volatility", FALLBACK_VOL))
    tvl = float(pool.get("tvl", 0.0))
    ratio = float(pool.get("apy_vol_ratio", 1.0))

    penalty = 0.0
    if bridging_needed:
        penalty += bridging_penalty
    if bridging_needed and is_lsd:
        penalty += lsd_penalty

    cf_term = beta * (chain_factor - 1.0)
    tvl_term = gamma * math.log10(max(1.0, tvl))

    score = apy - alpha * vol - cf_term + tvl_term + delta * ratio - penalty
    return score


def rank_pools(pools: List[Dict[str, Any]]) -> None:
    """
    Sort synergy pools in-place by descending HPC aggregator synergy score.
    Adds the 'score' field to each pool.

    :param pools: List of synergy pool dicts.
    """
    for p in pools:
        p["score"] = final_score_formula(p)
    pools.sort(key=lambda x: x["score"], reverse