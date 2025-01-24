import asyncio
from heavy.aggregator_hpc_async import produce_aggregator_output_async
from heavy.concurrency_throttler import ConcurrencyThrottler
from heavy.core.config_tools import load_aggregator_hpc_config

async def main():
    # Load configuration
    config = load_aggregator_hpc_config("src/heavy/aggregator_hpc_config.json")

    # Create a ConcurrencyThrottler instance (optional, but recommended)
    concurrency_throttler = ConcurrencyThrottler(
        max_requests_per_minute=float(config.get("maxRequestsPerMinute", 300)),
        max_tokens_per_minute=float(config.get("maxTokensPerMinute", 1500)),
        rate_limit_backoff_seconds=float(config.get("rateLimitBackoffSeconds", 5.0))
    )

    # Run the aggregator
    result_msg = await produce_aggregator_output_async(concurrency_throttler)
    print(result_msg)

if __name__ == "__main__":
    asyncio.run(main())