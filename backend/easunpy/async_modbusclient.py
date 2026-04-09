import asyncio
import logging
import socket
import time

# Set up logging
logger = logging.getLogger(__name__)

class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol for UDP discovery of the inverter."""
    def __init__(self, inverter_ip, message):
        self.transport = None
        self.inverter_ip = inverter_ip
        self.message = message
        self.response_received = asyncio.get_event_loop().create_future()

    def connection_made(self, transport):
        self.transport = transport
        logger.debug(f"Sending UDP discovery message to {self.inverter_ip}:58899")
        self.transport.sendto(self.message)

    def datagram_received(self, _data, addr):
        logger.info(f"Received response from {addr}")
        self.response_received.set_result(True)

    def error_received(self, exc):
        logger.error(f"Error received: {exc}")
        self.response_received.set_result(False)

class AsyncModbusClient:
    def __init__(self, inverter_ip: str, local_ip: str, port: int = 8899):
        self.inverter_ip = inverter_ip
        self.local_ip = local_ip
        self.port = port
        self._lock = asyncio.Lock()
        self._server = None
        self._active_connections = set()
        self._reader = None
        self._writer = None
        self._connection_established = False
        self._ever_connected = False  # True after the first successful TCP connection
        self._last_activity = 0

    async def _drop_connection(self):
        """Close the active TCP client connection but keep the server listening."""
        for writer in self._active_connections.copy():
            try:
                if not writer.is_closing():
                    writer.close()
                    await writer.wait_closed()
            except Exception as e:
                logger.debug(f"Error closing writer: {e}")
            finally:
                self._active_connections.discard(writer)
        self._connection_established = False
        self._reader = None
        self._writer = None

    async def _cleanup_server(self):
        """Cleanup server and all active connections."""
        try:
            # Close all active connections
            for writer in self._active_connections.copy():
                try:
                    if not writer.is_closing():
                        writer.close()
                        await writer.wait_closed()
                    else:
                        logger.debug("Connection already closed")
                except Exception as e:
                    logger.debug(f"Error closing connection: {e}")
                finally:
                    self._active_connections.remove(writer)

            # Close the server
            if self._server:
                try:
                    if self._server.is_serving():
                        self._server.close()
                        await self._server.wait_closed()
                        logger.debug("Server cleaned up successfully")
                    else:
                        logger.debug("Server already closed")
                except Exception as e:
                    logger.debug(f"Error closing server: {e}")
                finally:
                    self._server = None
        except Exception as e:
            logger.debug(f"Error during cleanup: {e}")
        finally:
            self._server = None
            self._active_connections.clear()
            self._connection_established = False
            self._ever_connected = False  # force UDP discovery on next connect attempt
            self._reader = None
            self._writer = None

    async def _find_available_port(self, start_port: int = 8899, max_attempts: int = 20) -> int:
        """Find an available port starting from the given port."""
        for port in range(start_port, start_port + max_attempts):
            try:
                # Test if port is available
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((self.local_ip, port))
                sock.close()
                return port
            except OSError:
                continue
        raise RuntimeError(f"No available ports found between {start_port} and {start_port + max_attempts}")

    async def send_udp_discovery(self) -> bool:
        """Perform UDP discovery with a fixed 15 s timeout per attempt."""
        timeout = 15
        loop = asyncio.get_running_loop()
        message = f"set>server={self.local_ip}:{self.port};".encode()

        for attempt in range(3):
            if self._connection_established:
                return True

            try:
                transport, protocol = await loop.create_datagram_endpoint(
                    lambda: DiscoveryProtocol(self.inverter_ip, message),
                    remote_addr=(self.inverter_ip, 58899)
                )

                try:
                    await asyncio.wait_for(protocol.response_received, timeout=timeout)
                    result = protocol.response_received.result()
                    if result or self._connection_established:
                        return True
                except asyncio.TimeoutError:
                    logger.warning(f"UDP discovery timeout (attempt {attempt + 1})")
                finally:
                    transport.close()

                if not self._connection_established:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"UDP discovery error: {str(e)}")

        logger.error("UDP discovery failed after all attempts")
        return False

    async def _ensure_connection(self) -> bool:
        """Ensure we have a valid TCP connection from the inverter.

        Strategy:
        1. Already connected → return immediately.
        2. After first-ever connection: inverter remembers our server address and
           will reconnect on its own — just wait, no UDP needed.
        3. First time ever: start TCP server, send UDP discovery, wait for connect.
        """
        if self._connection_established:
            return True

        try:
            # --- Ensure the TCP server is listening ---
            if not (self._server and self._server.is_serving()):
                self.port = await self._find_available_port(self.port)
                self._server = await asyncio.start_server(
                    self._handle_client_connection,
                    self.local_ip, self.port,
                )
                logger.info(f"TCP server started on {self.local_ip}:{self.port}")

            # --- After first connect: inverter knows our address, just wait ---
            if self._ever_connected:
                logger.info("TCP connection lost — waiting for inverter to reconnect…")
                try:
                    await asyncio.wait_for(self._wait_for_connection(), timeout=30)
                    return self._connection_established
                except asyncio.TimeoutError:
                    logger.error("Inverter did not reconnect within 30 s")
                    await self._cleanup_server()
                    return False

            # --- First time: send UDP discovery so inverter learns our address ---
            logger.info("First connection — sending UDP discovery…")
            await self.send_udp_discovery()  # best-effort

            if not self._connection_established:
                try:
                    await asyncio.wait_for(self._wait_for_connection(), timeout=15)
                except asyncio.TimeoutError:
                    logger.error("Timeout waiting for inverter TCP connection")
                    await self._cleanup_server()
                    return False

        except Exception as e:
            logger.error(f"Error establishing connection: {e}")
            await self._cleanup_server()
            return False

        return self._connection_established

    async def _wait_for_connection(self):
        """Wait for a client connection to be established."""
        while not self._connection_established:
            await asyncio.sleep(0.1)

    async def _handle_client_connection(self, reader, writer):
        """Handle incoming client connection."""
        if self._connection_established:
            logger.warning("Connection already established, closing new connection")
            writer.close()
            await writer.wait_closed()
            return

        self._reader = reader
        self._writer = writer
        self._connection_established = True
        self._ever_connected = True
        self._last_activity = time.time()
        self._active_connections.add(writer)
        logger.info("Client connection established")

    async def send_bulk(self, hex_commands: list[str], retry_count: int = 5) -> list[str]:
        """Send multiple Modbus TCP commands using persistent connection."""
        async with self._lock:
            responses = []
            
            for attempt in range(retry_count):
                try:
                    if not await self._ensure_connection():
                        if attempt == retry_count - 1:
                            logger.error("Failed to establish connection after all attempts")
                            return []
                        await asyncio.sleep(1)
                        continue

                    for command in hex_commands:
                        try:
                            if self._writer.is_closing():
                                logger.warning("Connection closed while processing commands")
                                await self._drop_connection()
                                break

                            logger.debug(f"Sending command: {command}")
                            command_bytes = bytes.fromhex(command)
                            self._writer.write(command_bytes)
                            await self._writer.drain()

                            response = await asyncio.wait_for(self._reader.read(1024), timeout=5)
                            if len(response) >= 6:
                                expected_length = int.from_bytes(response[4:6], 'big') + 6
                                while len(response) < expected_length:
                                    chunk = await asyncio.wait_for(self._reader.read(1024), timeout=5)
                                    if not chunk:
                                        break
                                    response += chunk

                            logger.debug(f"Response: {response.hex()}")
                            responses.append(response.hex())
                            self._last_activity = time.time()
                            await asyncio.sleep(0.1)

                        except asyncio.TimeoutError:
                            logger.error(f"Timeout reading response for command: {command}")
                            await self._drop_connection()
                            break
                        except Exception as e:
                            logger.error(f"Error processing command {command}: {e}")
                            await self._drop_connection()
                            break

                    if len(responses) == len(hex_commands):
                        return responses

                except Exception as e:
                    logger.error(f"Bulk send error: {e}")
                    await self._drop_connection()
                
                await asyncio.sleep(1)

            return [] 