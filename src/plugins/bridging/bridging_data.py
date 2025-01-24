import os
import json
import requests
from typing import Dict, Any, Optional

from heavy.logger import log_event
from heavy.core.config_tools import ACTIVE_CONFIG

# Local path to bridging_config.json
BRIDGING_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "bridging_config.json"
)

def _local_load_bridging_config() -> Dict[str, Any]:
    """
    Loads the bridging configuration from the local JSON file.

    Returns:
        Dict[str, Any]: The bridging configuration data.

    Raises:
        FileNotFoundError: If the config file is not found.
        RuntimeError: If there is an error reading the config file.
    """
    if not os.path.exists(BRIDGING_CONFIG_PATH):
        raise FileNotFoundError(
            f"Bridging configuration file not found at {BRIDGING_CONFIG_PATH}"
        )
    try:
        with open(BRIDGING_CONFIG_PATH, "r") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise RuntimeError(
            f"Error reading bridging configuration from {BRIDGING_CONFIG_PATH}: {e}"
        )

# Module-level: read bridging_config.json once
_CONFIG_CACHE = _local_load_bridging_config()

# Fallback data from bridging_config.json
TOKEN_DECIMALS: Dict[str, int] = _CONFIG_CACHE.get("tokenDecimals", {})
CHAIN_RISK_MAP_JSON: Dict[str, float] = _CONFIG_CACHE.get("chainRiskMap", {})
AGGREGATOR_SETTINGS_JSON: Dict[str, Any] = _CONFIG_CACHE.get("aggregatorSettings", {})
AGGREGATOR_URL_JSON = AGGREGATOR_SETTINGS_JSON.get("aggregatorUrl", "https://api.hop.exchange")

# Merge environment-based config with bridging_config fallback
AGGREGATOR_URL = ACTIVE_CONFIG.get("HOP_API_BASE", AGGREGATOR_URL_JSON)

LSD_BRIDGING_MULTIPLIER = ACTIVE_CONFIG.get(
    "lsdBridgingMultiplier",
    AGGREGATOR_SETTINGS_JSON.get("lsdBridgingMultiplier", 1.0)
)

AGGREGATOR_SETTINGS = {
    **AGGREGATOR_SETTINGS_JSON,
    "lsdBridgingMultiplier": LSD_BRIDGING_MULTIPLIER,
}

CHAIN_RISK_MAP_ENV = ACTIVE_CONFIG.get("CHAIN_RISK_MAP", {})
CHAIN_RISK_MAP = {
    **CHAIN_RISK_MAP_JSON,
    **CHAIN_RISK_MAP_ENV
}

def reload_bridging_config() -> None:
    """
    Reloads the bridging configuration, applying environment-based overrides.
    """
    global _CONFIG_CACHE, TOKEN_DECIMALS, CHAIN_RISK_MAP, AGGREGATOR_SETTINGS
    global AGGREGATOR_URL_JSON, AGGREGATOR_URL, AGGREGATOR_SETTINGS_JSON, CHAIN_RISK_MAP_JSON

    new_data = _local_load_bridging_config()
    _CONFIG_CACHE = new_data

    TOKEN_DECIMALS = new_data.get("tokenDecimals", {})
    CHAIN_RISK_MAP_JSON = new_data.get("chainRiskMap", {})
    AGGREGATOR_SETTINGS_JSON = new_data.get("aggregatorSettings", {})
    AGGREGATOR_URL_JSON = AGGREGATOR_SETTINGS_JSON.get("aggregatorUrl", "https://api.hop.exchange")

    # Re-merge with environment-based config
    AGGREGATOR_URL = ACTIVE_CONFIG.get("HOP_API_BASE", AGGREGATOR_URL_JSON)
    LSD_BRIDGING_MULTIPLIER = ACTIVE_CONFIG.get(
        "lsdBridgingMultiplier",
        AGGREGATOR_SETTINGS_JSON.get("lsdBridgingMultiplier", 1.0)
    )
    CHAIN_RISK_MAP_ENV = ACTIVE_CONFIG.get("CHAIN_RISK_MAP", {})
    merged_chain_risk = {**CHAIN_RISK_MAP_JSON, **CHAIN_RISK_MAP_ENV}

    AGGREGATOR_SETTINGS = {
        **AGGREGATOR_SETTINGS_JSON,
        "lsdBridgingMultiplier": LSD_BRIDGING_MULTIPLIER
    }

    CHAIN_RISK_MAP = merged_chain_risk

    log_event("Bridging configuration reloaded with environment overrides.", "INFO")

def _is_lsd_token(token_symbol: str) -> bool:
    """
    Checks if a token is an LSD token.
    """
    return token_symbol.lower() in ("steth", "reth", "stmatic", "seth2", "ankr")

def _apply_lsd_cost_multiplier(base_fee: float, token_symbol: str) -> float:
    """
    Applies an LSD cost multiplier if the token is an LSD and such a multiplier is configured.
    """
    if _is_lsd_token(token_symbol):
        multiplier = AGGREGATOR_SETTINGS.get("lsdBridgingMultiplier", 1.0)
        log_event(f"Applying LSD multiplier: {multiplier}", "DEBUG")
        return base_fee * multiplier
    return base_fee

# Chain Slug Maps
numeric_chain_map = {
    "1": "ethereum",
    "10": "optimism",
    "42161": "arbitrum",
    "137": "polygon",
    "8453": "base",
    "100": "gnosis",
    "43114": "avalanche",
    "42170": "nova",
    "59144": "linea"
}

slug_chain_map = {
    "ethereum": "ethereum",
    "optimism": "optimism",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "base": "base",
    "gnosis": "gnosis",
    "avalanche": "avalanche",
    "nova": "nova",
    "linea": "linea"
}

def _normalize_chain_slug(chain_str: str) -> str:
    """
    Normalizes a chain identifier to a Hop Protocol compatible slug.

    :param chain_str: The chain identifier, either numeric or common name.
    :return: The normalized chain slug.
    """
    chain_str = chain_str.strip().lower()
    if chain_str in numeric_chain_map:
        return numeric_chain_map[chain_str]
    if chain_str in ["sepolia", "base-sepolia"]:
        return "ethereum"
    if chain_str not in slug_chain_map:
        raise ValueError(f"Unsupported chain identifier: {chain_str}")
    return slug_chain_map[chain_str]

def _to_smallest_units(token_symbol: str, amount_float: float) -> str:
    """
    Converts a floating-point amount to an integer string in the smallest token units.
    """
    symbol_lower = token_symbol.lower()
    decimals = TOKEN_DECIMALS.get(symbol_lower)
    if decimals is None:
        raise ValueError(
            f"No decimals information found for token: {token_symbol}"
        )
    scaled_amount = int(round(amount_float * (10 ** decimals)))
    return str(scaled_amount)

def _from_smallest_units(token_symbol: str, amount_int: int) -> float:
    """
    Converts an integer amount in smallest units to a float based on token decimals.
    """
    symbol_lower = token_symbol.lower()
    decimals = TOKEN_DECIMALS.get(symbol_lower)
    if decimals is None:
        raise ValueError(
            f"No decimals information found for token: {token_symbol}"
        )
    return float(amount_int) / (10 ** decimals)

def scale_amount_to_smallest_units(token_symbol: str, amount_float: float) -> int:
    """
    Converts and scales an amount to the smallest token units as an integer.
    """
    return int(_to_smallest_units(token_symbol, amount_float))

def scale_amount_from_smallest_units(token_symbol: str, amount_int: int) -> float:
    """
    Converts an amount from the smallest token units back to a float.
    """
    return _from_smallest_units(token_symbol, amount_int)

def fetch_bridging_fees(
    from_chain: str,
    to_chain: str,
    token_symbol: str,
    amount_float: float,
    slippage: float = 0.5
) -> float:
    """
    Fetches bridging fee quotes using the specified aggregator URL.

    :param from_chain: Source chain identifier.
    :param to_chain: Destination chain identifier.
    :param token_symbol: Token symbol for the bridging operation.
    :param amount_float: Amount of tokens to bridge.
    :param slippage: Allowed slippage for the bridging operation.
    :return: Estimated bridging fee in token units.
    """
    from_slug = _normalize_chain_slug(from_chain)
    to_slug = _normalize_chain_slug(to_chain)
    scaled_amount = _to_smallest_units(token_symbol, amount_float)

    quote_endpoint = f"{AGGREGATOR_URL.rstrip('/')}/v1/quote"
    params = {
        "amount": scaled_amount,
        "token": token_symbol.lower(),
        "fromChain": from_slug,
        "toChain": to_slug,
        "slippage": str(slippage)
    }
    log_event(f"Fetching bridging fees from {quote_endpoint} with params: {params}")

    try:
        resp = requests.get(quote_endpoint, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        log_event(f"Error fetching bridging fees: {e}")
        raise ValueError(f"Failed to fetch bridging fees: {e}")

    fee_str = data.get("fee")
    if not fee_str:
        log_event(f"No 'fee' found in response. Keys: {list(data.keys())}")
        raise ValueError("No valid 'fee' found in the aggregator response.")

    fee_int = int(fee_str)
    aggregator_fee_base = _from_smallest_units(token_symbol, fee_int)
    aggregator_fee = _apply_lsd_cost_multiplier(aggregator_fee_base, token_symbol)

    log_event(f"Bridging fee from {from_slug} to {to_slug} for {token_symbol}: Base Fee = {aggregator_fee_base:.6f}, Adjusted Fee = {aggregator_fee:.6f}")
    return aggregator_fee

def get_chain_risk_factor(chain: str) -> float:
    """
    Retrieves the chain risk factor from the merged CHAIN_RISK_MAP.

    :param chain: The chain identifier.
    :return: The risk factor for the chain, defaulting to 1.0 if not found.
    """
    return CHAIN_RISK_MAP.get(chain.lower(), 1.0)