import asyncio
import logging
import json
import uuid
import time
from typing import Dict, Any, Callable, Awaitable, Optional, Set, List
from websockets.server import WebSocketServerProtocol, serve
import websockets

from src.metrics import MetricsCollector, time_request

logger = logging.getLogger('heavy-websocket')

MessageHandler = Callable[[str, Any, WebSocketServerProtocol], Awaitable[None]]

class WebSocketServer:
    """
    WebSocket server for real-time communication.
    
    This server manages WebSocket connections, handles client interactions, and
    routes requests to appropriate handlers based on action types.
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        """
        Initialize WebSocket server.
        
        Args:
            host: Host address to listen on
            port: Port to listen on
        """
        self.host = host
        self.port = port
        self.active_connections: Set[WebSocketServerProtocol] = set()
        self.handlers: Dict[str, MessageHandler] = {}
        self.server = None
        self._background_tasks: List[asyncio.Task] = []
        
    def register_handler(self, message_type: str, handler: MessageHandler) -> None:
        """
        Register a handler for a specific message type.
        
        Args:
            message_type: Type of message to handle
            handler: Handler function
        """
        self.handlers[message_type] = handler
        logger.info(f"Registered handler for message type: {message_type}")
        
    async def _connection_handler(self, websocket: WebSocketServerProtocol, path: str) -> None:
        """
        Handle a new WebSocket connection.
        
        Args:
            websocket: WebSocket connection
            path: Connection path
        """
        client_info = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        logger.info(f"New connection from {client_info}")
        
        # Add to active connections and update metrics
        self.active_connections.add(websocket)
        MetricsCollector.set_active_connections(len(self.active_connections))
        
        try:
            await self._message_loop(websocket)
        finally:
            # Remove from active connections and update metrics
            self.active_connections.remove(websocket)
            MetricsCollector.set_active_connections(len(self.active_connections))
            logger.info(f"Connection closed from {client_info}")
    
    async def _message_loop(self, websocket: WebSocketServerProtocol) -> None:
        """
        Process messages from a WebSocket connection.
        
        Args:
            websocket: WebSocket connection
        """
        async for message in websocket:
            start_time = time.time()
            try:
                data = json.loads(message)
                
                if "type" not in data:
                    logger.warning(f"Received message without type: {message}")
                    continue
                
                message_type = data["type"]
                with time_request(f"ws/{message_type}", "websocket"):
                    if message_type in self.handlers:
                        # Execute handler in a task to avoid blocking
                        task = asyncio.create_task(
                            self.handlers[message_type](message_type, data, websocket)
                        )
                        # Store background task to avoid being garbage collected
                        self._background_tasks.append(task)
                        task.add_done_callback(lambda t: self._background_tasks.remove(t))
                    else:
                        logger.warning(f"No handler for message type: {message_type}")
                        
                        # Send error response
                        await self.send_error(
                            websocket, 
                            f"Unsupported message type: {message_type}",
                            data.get("request_id")
                        )
                
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON: {message}")
                await self.send_error(websocket, "Invalid JSON", None)
            except Exception as e:
                logger.exception(f"Error processing message: {e}")
                await self.send_error(websocket, f"Internal error: {str(e)}", None)
            
            # Record request latency
            latency = time.time() - start_time
            logger.debug(f"Message processing time: {latency:.4f}s")
    
    async def broadcast(self, message: Dict[str, Any]) -> None:
        """
        Broadcast a message to all connected clients.
        
        Args:
            message: Message to broadcast
        """
        if not self.active_connections:
            logger.debug("No active connections for broadcast")
            return
            
        json_message = json.dumps(message)
        
        send_tasks = []
        for websocket in self.active_connections:
            send_tasks.append(asyncio.create_task(self._safe_send(websocket, json_message)))
            
        await asyncio.gather(*send_tasks, return_exceptions=True)
        
        logger.debug(f"Broadcast message to {len(self.active_connections)} clients")
    
    async def _safe_send(self, websocket: WebSocketServerProtocol, message: str) -> None:
        """
        Safely send a message to a client, handling any errors.
        
        Args:
            websocket: WebSocket connection
            message: Message to send
        """
        try:
            await websocket.send(message)
        except websockets.exceptions.ConnectionClosed:
            logger.debug(f"Could not send message to closed connection")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
    
    async def send_response(
        self, 
        websocket: WebSocketServerProtocol, 
        message_type: str, 
        data: Dict[str, Any], 
        request_id: Optional[str] = None
    ) -> None:
        """
        Send a response to a client.
        
        Args:
            websocket: WebSocket connection
            message_type: Type of message
            data: Message data
            request_id: Request ID to correlate with request
        """
        response = {
            "type": message_type,
            "data": data,
            "timestamp": time.time()
        }
        
        if request_id:
            response["request_id"] = request_id
            
        try:
            await websocket.send(json.dumps(response))
        except Exception as e:
            logger.error(f"Error sending response: {e}")
            
    async def send_error(
        self, 
        websocket: WebSocketServerProtocol, 
        error_message: str, 
        request_id: Optional[str] = None
    ) -> None:
        """
        Send an error response to a client.
        
        Args:
            websocket: WebSocket connection
            error_message: Error message
            request_id: Request ID to correlate with request
        """
        await self.send_response(
            websocket,
            "error",
            {"message": error_message},
            request_id
        )
        
    async def start(self) -> None:
        """Start the WebSocket server."""
        logger.info(f"Starting WebSocket server on {self.host}:{self.port}")
        
        self.server = await serve(
            self._connection_handler,
            self.host,
            self.port,
            ping_interval=30,  # Send pings every 30 seconds
            ping_timeout=10,   # Wait 10 seconds for pong response
            close_timeout=5    # Wait 5 seconds for graceful close
        )
        
        # Reset the connection counter when starting the server
        MetricsCollector.set_active_connections(0)
        
        logger.info("WebSocket server started")
        
    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if self.server:
            logger.info("Stopping WebSocket server")
            
            # Close all active connections
            close_tasks = []
            for websocket in self.active_connections:
                close_tasks.append(asyncio.create_task(websocket.close(1001, "Server shutting down")))
                
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)
                
            # Cancel all background tasks
            for task in self._background_tasks:
                task.cancel()
                
            try:
                await asyncio.gather(*self._background_tasks, return_exceptions=True)
            except asyncio.CancelledError:
                pass
                
            # Close the server
            self.server.close()
            await self.server.wait_closed()
            
            # Reset metrics
            MetricsCollector.set_active_connections(0)
            
            logger.info("WebSocket server stopped")
            
class WebSocketBridge:
    """
    Bridge for WebSocket communication between components.
    
    This class manages the WebSocket server and provides a simplified interface
    for registering handlers and broadcasting events.
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        """Initialize the WebSocket bridge."""
        self.server = WebSocketServer(host, port)
        self.logger = logging.getLogger("heavy.websocket_bridge")
        self._register_default_handlers()
        
    def _register_default_handlers(self) -> None:
        """Register default handlers for common actions."""
        self.server.register_handler("ping", self._handle_ping)
        self.server.register_handler("status", self._handle_status)
        
    async def _handle_ping(self, data: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """Handle ping requests."""
        return {"pong": True, "timestamp": asyncio.get_event_loop().time()}
        
    async def _handle_status(self, data: Dict[str, Any], client_id: str) -> Dict[str, Any]:
        """Handle status requests."""
        return {
            "status": "ok",
            "clients": len(self.server.active_connections),
            "uptime": asyncio.get_event_loop().time()
        }
        
    def register_handler(self, 
                    action: str, 
                    handler: Callable[[Dict[str, Any], str], Awaitable[Dict[str, Any]]]) -> None:
        """
        Register a handler for an action.
        
        Args:
            action: The action to handle
            handler: The handler function
        """
        self.server.register_handler(action, handler)
        
    async def broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Broadcast an event to all connected clients.
        
        Args:
            event_type: The type of event
            data: The event data
        """
        await self.server.broadcast(data)
        
    async def start(self) -> None:
        """Start the WebSocket bridge."""
        await self.server.start()
        
    async def stop(self) -> None:
        """Stop the WebSocket bridge."""
        await self.server.stop() 