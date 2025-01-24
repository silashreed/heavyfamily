import time
import asyncio
import logging

class ConcurrencyThrottler:
    """
    A token-bucket style concurrency manager that enforces:

    - `max_requests_per_minute`: How many discrete requests (units of concurrency) are allowed per minute.
    - `max_tokens_per_minute`: How many tokens (a more granular measure of concurrency usage) are allowed per minute.

    The capacity refills over time. Each second, the available capacity for both requests and tokens is 
    increased by `max_requests_per_minute / 60` and `max_tokens_per_minute / 60`, respectively.

    If an operation would exceed the available capacity, the `acquire` method will asynchronously wait 
    until sufficient capacity is replenished.

    Additionally, a short backoff mechanism is implemented to handle rate-limit (429) errors from a target service. 
    When such an error is detected, new requests will be delayed for `rate_limit_backoff_seconds`.

    Typical usage in HPC aggregator synergy or LSD aggregator synergy:

    1. Initialize `ConcurrencyThrottler` with suitable rates.
    2. Before each I/O call, await `concurrency_throttler.acquire(tokens_needed=some_int)`.
    3. If a 429 error is received, call `concurrency_throttler.note_rate_limit_error()`.
    """

    def __init__(
        self,
        max_requests_per_minute: float,
        max_tokens_per_minute: float,
        rate_limit_backoff_seconds: float = 15.0,
        logger=None
    ):
        """
        Initializes the ConcurrencyThrottler.

        :param max_requests_per_minute: Maximum number of requests allowed per minute. Each call to `acquire` consumes 
                                         one request's worth of capacity.
        :param max_tokens_per_minute: Maximum number of tokens that can be consumed per minute. The `tokens_needed` 
                                       argument in `acquire` allows for weighted requests, where some operations 
                                       might consume more tokens than others based on their complexity or resource 
                                       intensity.
        :param rate_limit_backoff_seconds: Duration of the cooldown period (in seconds) after a 429 error is detected.
        :param logger: Optional logger instance for logging messages. If not provided, a default logger is used.
        """
        self.max_requests_per_minute = max_requests_per_minute
        self.max_tokens_per_minute = max_tokens_per_minute
        self.rate_limit_backoff_seconds = rate_limit_backoff_seconds

        self.available_requests = max_requests_per_minute
        self.available_tokens = max_tokens_per_minute

        self.last_update_time = time.time()
        self.logger = logger or logging.getLogger(__name__)

        self.time_of_last_rate_limit_error = 0.0

    def refill_capacity(self):
        """
        Refills the available capacity for requests and tokens based on the elapsed time since the last update.
        The capacity is refilled at a rate proportional to `max_requests_per_minute` and `max_tokens_per_minute`.
        """
        current_time = time.time()
        elapsed = current_time - self.last_update_time
        self.last_update_time = current_time

        req_refill = (self.max_requests_per_minute * elapsed) / 60.0
        tok_refill = (self.max_tokens_per_minute * elapsed) / 60.0

        self.available_requests = min(self.available_requests + req_refill, self.max_requests_per_minute)
        self.available_tokens = min(self.available_tokens + tok_refill, self.max_tokens_per_minute)

    async def acquire(self, tokens_needed: int):
        """
        Acquires capacity for a single request, along with the specified number of tokens. 
        If the required capacity is not immediately available, this method will asynchronously wait, 
        periodically checking for sufficient capacity and refilling based on elapsed time. 
        It also enforces a cooldown period if a rate limit error was recently encountered.

        :param tokens_needed: An integer representing the weight or cost of the request in tokens.
        """
        while True:
            self.refill_capacity()

            since_rate_limit = time.time() - self.time_of_last_rate_limit_error
            if since_rate_limit < self.rate_limit_backoff_seconds:
                cooldown_remaining = self.rate_limit_backoff_seconds - since_rate_limit
                self.logger.warning(
                    f"ConcurrencyThrottler: Cooling down for {cooldown_remaining:.1f}s due to a recent rate-limit error."
                )
                await asyncio.sleep(cooldown_remaining)
                continue

            if self.available_requests >= 1 and self.available_tokens >= tokens_needed:
                self.available_requests -= 1
                self.available_tokens -= tokens_needed
                return

            await asyncio.sleep(0.05)

    def note_rate_limit_error(self):
        """
        Records the timestamp of a rate limit error (e.g., HTTP 429 response). 
        This triggers a cooldown period in the `acquire` method.
        """
        self.time_of_last_rate_limit_error = time.time()
        self.logger.warning("ConcurrencyThrottler: Rate-limit error noted. Future calls will temporarily cool down.")