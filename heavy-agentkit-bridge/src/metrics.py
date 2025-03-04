import logging
import time
import asyncio
from asyncio import Task
from contextlib import contextmanager
from typing import Dict, Any, Optional, List, Generator, Union, Callable

import aiohttp
from aiohttp import web
from prometheus_client import Counter, Gauge, Histogram
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

logger = logging.getLogger('heavy-metrics')

# Define metrics
WALLET_OPERATIONS = Counter(
    'wallet_operations_total',
    'Total number of wallet operations performed',
    ['operation', 'network', 'status']
)

WALLET_COUNT = Gauge(
    'wallet_count',
    'Number of wallets being managed',
    ['network']
)

WALLET_BALANCE = Gauge(
    'wallet_balance',
    'Wallet balance in tokens',
    ['wallet_id', 'token', 'network']
)

REQUEST_LATENCY = Histogram(
    'request_latency_seconds',
    'Request latency in seconds',
    ['endpoint', 'method'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30, 60]
)

ACTIVE_CONNECTIONS = Gauge(
    'active_connections',
    'Number of active WebSocket connections'
)

API_REQUESTS = Counter(
    'api_requests_total',
    'Total number of API requests',
    ['endpoint', 'method', 'status']
)

CDP_API_CALLS = Counter(
    'cdp_api_calls_total',
    'Total number of Coinbase Developer Platform API calls',
    ['endpoint', 'method', 'status']
)

class MetricsService:
    """Service for exposing Prometheus metrics and health endpoints."""
    
    def __init__(self, host: str = '0.0.0.0', port: int = 8000):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self._setup_routes()
        self._background_tasks: List[Task] = []
        
    def _setup_routes(self) -> None:
        """Set up the routes for the metrics service."""
        self.app.add_routes([
            web.get('/metrics', self.metrics_handler),
            web.get('/health', self.health_handler),
        ])
        
    async def metrics_handler(self, request: web.Request) -> web.Response:
        """Handler for /metrics endpoint."""
        resp = web.Response(body=generate_latest())
        resp.content_type = CONTENT_TYPE_LATEST
        return resp
    
    async def health_handler(self, request: web.Request) -> web.Response:
        """Handler for /health endpoint."""
        return web.json_response({
            'status': 'healthy',
            'timestamp': time.time()
        })
    
    async def start(self) -> None:
        """Start the metrics service."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        
        # Start background tasks
        task = asyncio.create_task(self._collect_metrics_periodically())
        self._background_tasks.append(task)
        
        logger.info(f"Metrics service started on http://{self.host}:{self.port}")
    
    async def stop(self) -> None:
        """Stop the metrics service."""
        # Cancel background tasks
        for task in self._background_tasks:
            task.cancel()
            
        try:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
            
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("Metrics service stopped")
    
    async def _collect_metrics_periodically(self) -> None:
        """Collect metrics periodically in the background."""
        try:
            while True:
                # This could be used to update any metrics that need periodic collection
                await asyncio.sleep(15)  # Collect every 15 seconds
        except asyncio.CancelledError:
            logger.debug("Metrics collection task cancelled")
            raise

class MetricsCollector:
    """Static methods for recording various metrics."""
    
    @staticmethod
    def record_wallet_operation(
        operation: str, 
        network: str, 
        status: str = "success"
    ) -> None:
        """Record a wallet operation metric."""
        WALLET_OPERATIONS.labels(operation=operation, network=network, status=status).inc()
    
    @staticmethod
    def set_wallet_count(network: str, count: int) -> None:
        """Set the current wallet count for a network."""
        WALLET_COUNT.labels(network=network).set(count)
    
    @staticmethod
    def set_wallet_balance(
        wallet_id: str, 
        token: str, 
        network: str, 
        balance: float
    ) -> None:
        """Set the current balance for a wallet and token."""
        WALLET_BALANCE.labels(wallet_id=wallet_id, token=token, network=network).set(balance)
    
    @staticmethod
    def record_api_request(
        endpoint: str, 
        method: str, 
        status: str = "success"
    ) -> None:
        """Record an API request metric."""
        API_REQUESTS.labels(endpoint=endpoint, method=method, status=status).inc()
    
    @staticmethod
    def record_cdp_api_call(
        endpoint: str, 
        method: str, 
        status: str = "success"
    ) -> None:
        """Record a CDP API call metric."""
        CDP_API_CALLS.labels(endpoint=endpoint, method=method, status=status).inc()
    
    @staticmethod
    def set_active_connections(count: int) -> None:
        """Set the current number of active WebSocket connections."""
        ACTIVE_CONNECTIONS.set(count)
    
    @staticmethod
    def observe_request_latency(
        endpoint: str, 
        method: str, 
        seconds: float
    ) -> None:
        """Record the latency of a request."""
        REQUEST_LATENCY.labels(endpoint=endpoint, method=method).observe(seconds)

class TimingContextManager:
    """Context manager for timing operations and recording metrics."""
    
    def __init__(self, endpoint: str, method: str):
        self.endpoint = endpoint
        self.method = method
        self.start_time = 0.0
    
    def __enter__(self) -> 'TimingContextManager':
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        elapsed = time.time() - self.start_time
        MetricsCollector.observe_request_latency(
            endpoint=self.endpoint,
            method=self.method,
            seconds=elapsed
        )
        
        # Record API request status
        status = "error" if exc_type else "success"
        MetricsCollector.record_api_request(
            endpoint=self.endpoint,
            method=self.method,
            status=status
        )

# Convenience functions
def time_request(endpoint: str, method: str) -> TimingContextManager:
    """Convenience function for timing requests."""
    return TimingContextManager(endpoint, method)

@contextmanager
def time_wallet_operation(
    operation: str, 
    network: str
) -> Generator[None, None, None]:
    """Convenience context manager for timing wallet operations."""
    start_time = time.time()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        elapsed = time.time() - start_time
        MetricsCollector.observe_request_latency(
            endpoint=f"wallet/{operation}",
            method="wallet",
            seconds=elapsed
        )
        MetricsCollector.record_wallet_operation(
            operation=operation,
            network=network,
            status=status
        )

# Metric service instance and functions
_metrics_service: Optional[MetricsService] = None

def setup_default_metrics() -> None:
    """Initialize default metrics with appropriate initial values."""
    # Set initial values for gauges if needed
    MetricsCollector.set_active_connections(0)
    logger.info("Default metrics initialized")

async def start_metrics_service(
    host: str = '0.0.0.0', 
    port: int = 8000
) -> MetricsService:
    """Start the metrics service."""
    global _metrics_service
    
    if _metrics_service is None:
        _metrics_service = MetricsService(host=host, port=port)
        await _metrics_service.start()
    
    return _metrics_service

async def stop_metrics_service() -> None:
    """Stop the metrics service if it's running."""
    global _metrics_service
    
    if _metrics_service is not None:
        await _metrics_service.stop()
        _metrics_service = None
        logger.info("Metrics service stopped") 