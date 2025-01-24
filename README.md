# Autonomous DeFi Agent: Asynchronous Aggregator and Risk-Based Token Vetting

## Overview

This repository contains a subset of an experimental Autonomous DeFi Agent designed to identify and evaluate yield farming opportunities across various blockchains. The core of this release is the **asynchronous High-Performance Computing (HPC) aggregator** and the **Risk Agent**, which work together to discover, filter, and rank DeFi pools, and to vet newly discovered tokens based on on-chain data.

**Disclaimer:** This is experimental software and should be used with extreme caution. It is not intended for use with real funds without thorough testing and a deep understanding of the code. The author is not responsible for any financial losses incurred through the use of this software. This is not financial advice.

## Key Features

*   **Asynchronous Aggregator (`aggregator_hpc_async.py`):**
    *   Asynchronously fetches yield farming pool data from DeFiLlama.
    *   Optionally merges data from LSD subgraphs.
    *   Calculates historical volatility.
    *   Augments pool data with features like `bridging_needed`, `chain_factor`, and `apy_vol_ratio`.
    *   Filters out low-quality pools based on configurable thresholds (APY, TVL, chain risk).
    *   Ranks pools based on a configurable scoring formula that considers APY, volatility, TVL, and chain risk.
    *   Stores the results in `aggregator_output.json`.
    *   Uses a `ConcurrencyThrottler` to manage API request rates and avoid rate limiting.

*   **Risk Agent (`risk_agent.py`):**
    *   Provides autonomous risk assessment for newly discovered tokens.
    *   Performs the following checks using on-chain data and external APIs:
        *   **Liquidity Check:** Verifies sufficient liquidity on reputable DEXs using the DexScreener API.
        *   **Volume Check:** Ensures sufficient 24-hour trading volume using the DexScreener API.
        *   **Holder Distribution:** Analyzes token holder distribution using Etherscan and Covalent APIs to identify potential risks.
    *   Returns `True` if a token passes all checks, `False` otherwise.

*   **Bridging Data (`bridging_data.py`):**
    *   Provides helper functions for calculating bridging costs.
    *   Stores chain risk factors and token decimal information.
    *   Fetches bridging quotes from the Hop Protocol API.

## Technology Stack

*   **Python 3.9+** with `asyncio` and `aiohttp` for asynchronous operations.
*   **DeFiLlama API:** For fetching yield farming pool data.
*   **DexScreener API:** For checking token liquidity and volume.
*   **Etherscan API:** For fetching token supply data.
*   **Covalent API:** For fetching token holder data.
*   **Hop Protocol API:** For fetching bridging quotes.
*   **Pydantic:** For data validation and defining tool input schemas.

## Installation

1.  **Clone the repository:**

    ```bash
    git clone <repository_url>
    cd <repository_name>
    ```

2.  **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

3.  **Set up environment variables:**

    *   Create a `.env` file in the root directory of the project.
    *   Add the following environment variables, replacing the placeholder values with your actual API keys:

    ```
    ETHERSCAN_API_KEY=YOUR_ETHERSCAN_API_KEY
    ARBISCAN_API_KEY=YOUR_ARBISCAN_API_KEY
    POLYGONSCAN_API_KEY=YOUR_POLYGONSCAN_API_KEY
    COINGECKO_API_KEY=YOUR_COINGECKO_API_KEY
    COINMARKETCAP_API_KEY=YOUR_COINMARKETCAP_API_KEY
    BASESCAN_API_KEY=YOUR_BASESCAN_API_KEY
    COVALENT_API_KEY=YOUR_COVALENT_API_KEY
    # ... other environment variables as needed ...
    ```

## Configuration

The aggregator's behavior can be configured using the `aggregator_hpc_config.json` file. Here are some of the key parameters:

*   **`lsdMerges`:**  Enable/disable merging of LSD data.
*   **`fallbackVol`:** Default volatility used when historical data is insufficient.
*   **`maxFetchRetries`:** Maximum number of retries for fetching data from DeFiLlama.
*   **`maxPools`:** Maximum number of pools to process.
*   **`minApy`:** Minimum APY for a pool to be considered.
*   **`minTvl`:** Minimum TVL for a pool to be considered.
*   **`maxChainFactor`:** Maximum chain risk factor for a pool to be considered.
*   **`aggregatorWeights`:** Weights used in the pool ranking formula (alpha, beta, gamma, delta, bridgingPenalty, lsdBridgingMultiplierPenalty).
*   **`maxRequestsPerMinute`:**  For the `ConcurrencyThrottler`.
*   **`maxTokensPerMinute`:** For the `ConcurrencyThrottler`.
*   **`rateLimitBackoffSeconds`:** For the `ConcurrencyThrottler`.

**Risk Agent Thresholds:**
The following thresholds are used by the `RiskAgent` when assessing new tokens:

*   **`minLiquidityUsd`:** Minimum liquidity in USD (default: 50000.0).
*   **`minVolume24hUsd`:** Minimum 24-hour trading volume in USD (default: 20000.0).
*   **`minUniqueHolders`:** Minimum number of unique token holders (default: 500).
*   **`maxTopHoldersPercent`:** Maximum percentage of tokens held by the top 10 holders (default: 50.0).

**Bridging Configuration**
*   **`tokenDecimals`:** Decimal precision for various tokens.
*   **`chainRiskMap`:** Risk factors associated with different chains.
*   **`aggregatorSettings`:** Settings for the bridging aggregator, including API URL, timeout, and default slippage.
*   **`bridgeRoutes`:** Configuration for specific bridge routes, including type, token, chain IDs, daily limits, base fees, and risk factors.

## Usage

The `example_usage.py` script demonstrates how to use the aggregator:

```python
import asyncio
from heavy.aggregator_hpc_async import produce_aggregator_output_async
from heavy.concurrency_throttler import ConcurrencyThrottler

async def main():
    # Create a ConcurrencyThrottler instance (optional, but recommended)
    concurrency_throttler = ConcurrencyThrottler(
        max_requests_per_minute=300,
        max_tokens_per_minute=300,
        rate_limit_backoff_seconds=5.0
    )

    # Run the aggregator
    result_msg = await produce_aggregator_output_async(concurrency_throttler)
    print(result_msg)

if __name__ == "__main__":
    asyncio.run(main())
````

## Contributing

Contributions are welcome\! If you'd like to contribute, please follow these steps:

1.  Fork the repository.
2.  Create a new branch for your feature or bug fix.
3.  Make your changes and commit them with clear commit messages.
4.  Write tests for your changes.
5.  Submit a pull request.

## License

This project is licensed under the [MIT License](LICENSE) - see the LICENSE file for details.

## Contact

For questions or feedback, please open an issue on GitHub.
