import os
import json
import math
import statistics
import requests
from typing import List, Dict, Any
from heavy.logger import log_event
import time

from heavy.historical_data_cache import fetch_pool_history_with_cache

def _do_fetch_remote_history(pool_id: str) -> List[Dict[str, Any]]:
    """
    Fetches historical pool data from yields.llama.fi, applying exponential backoff on 429 errors.

    :param pool_id: The ID of the pool to fetch history for.
    :return: A list of dictionaries, each representing a historical data point.
    :raises RuntimeError: If all fetch attempts fail.
    """
    url = f"https://yields.llama.fi/chart/{pool_id}"
    max_attempts = 5
    backoff = 1.0

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 429:
                if attempt < max_attempts:
                    log_event(
                        f"[historical_data] 429 error: retrying in {backoff}s, attempt {attempt}/{max_attempts}",
                        "WARNING"
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                else:
                    raise ValueError("429 Too Many Requests")

            resp.raise_for_status()
            return resp.json().get("data", [])

        except ValueError as ve:
            if "429" in str(ve):
                log_event(
                    f"[historical_data] 429 error: attempt {attempt}/{max_attempts}, backing off {backoff}s",
                    "WARNING"
                )
                if attempt < max_attempts:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                else:
                    raise  # Re-raise after max attempts
            else:
                log_event(f"[historical_data] Unexpected ValueError: {ve}", "ERROR")
                raise
        except Exception as e:
            log_event(
                f"[historical_data] Fetch failed on attempt {attempt}/{max_attempts}: {e}",
                "WARNING"
            )
            if attempt < max_attempts:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
            else:
                raise  # Re-raise after max attempts

    raise RuntimeError(f"Failed to fetch data for pool ID {pool_id} after multiple attempts")

def fetch_pool_history(pool_id: str) -> List[Dict[str, Any]]:
    """
    Fetches historical pool data, using the cache if available and fresh.

    :param pool_id: The ID of the pool to fetch history for.
    :return: A list of historical data points, or an empty list on failure.
    """
    try:
        data = fetch_pool_history_with_cache(pool_id, _do_fetch_remote_history)
        return data
    except Exception as e:
        log_event(f"[historical_data] Failed to fetch pool history for {pool_id}: {e}", "ERROR")
        return []

def compute_volatility_from_history(history: List[Dict[str, Any]]) -> float:
    """
    Computes volatility from historical APY data using log-return standard deviation.

    :param history: A list of historical data points, each with an 'apy' key.
    :return: The computed volatility, or 0.05 as a fallback if insufficient data.
    """
    if len(history) < 2:
        return 0.05

    log_returns = []
    for i in range(1, len(history)):
        prev_apy = history[i - 1].get("apy", 0.0)
        curr_apy = history[i].get("apy", 0.0)
        if prev_apy > 0 and curr_apy > 0:
            log_returns.append(math.log(curr_apy / prev_apy))

    if not log_returns:
        return 0.05

    return statistics.pstdev(log_returns)