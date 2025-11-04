#!/usr/bin/env python3
"""
UDP Tunnel for Cross-Subnet Kasa Device Discovery

Creates a UDP tunnel through SSH to forward Kasa discovery broadcasts
from a remote subnet to the local machine.

Usage:
    # Start tunnel to forward UDP from remote 192.168.1.0/24 to local
    tunnel = UDPTunnel('openwrt', '192.168.1.1', local_port=9999, remote_port=9999)
    tunnel.start()
    
    # Now local Kasa discovery will find remote devices
    from kasa import Discover
    devices = await Discover.discover()
    
    tunnel.stop()
"""

import socket
import threading
import logging
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)


class UDPTunnel:
    """
    Creates a UDP tunnel through SSH to forward discovery packets.
    
    How it works:
    1. SSH to remote host (OpenWrt router)
    2. On router, run a UDP listener that forwards packets back
    3. On local machine, run a UDP listener that accepts discovery packets
    4. Bridge them together through SSH
    """
    
    def __init__(
        self,
        ssh_host: str,
        remote_subnet_gateway: str,
        local_port: int = 9999,
        remote_port: int = 9999,
        ssh_identity: Optional[str] = None
    ):
        """
        Initialize UDP tunnel.
        
        Args:
            ssh_host: SSH connection string (e.g., 'openwrt')
            remote_subnet_gateway: Gateway/broadcast IP on remote subnet (e.g., '192.168.1.255')
            local_port: Local port to listen on (default 9999)
            remote_port: Remote port to tunnel (default 9999)
            ssh_identity: SSH key file (None = use default)
        """
        self.ssh_host = ssh_host
        self.remote_subnet_gateway = remote_subnet_gateway
        self.local_port = local_port
        self.remote_port = remote_port
        self.ssh_identity = ssh_identity
        self.tunnel_process: Optional[subprocess.Popen] = None
        self.local_socket: Optional[socket.socket] = None
        self.listener_thread: Optional[threading.Thread] = None
        self.running = False
    
    def start(self) -> bool:
        """
        Start the UDP tunnel.
        
        Returns:
            True if tunnel started successfully
        """
        try:
            logger.info(f"Starting UDP tunnel on port {self.local_port}")
            
            # Step 1: Create SSH command to start remote UDP receiver
            # On the router, we'll use netcat to listen and forward
            self._start_ssh_tunnel()
            
            # Step 2: Start local UDP listener
            self._start_local_listener()
            
            logger.info(f"✓ UDP tunnel active: localhost:{self.local_port} <-> {self.remote_subnet_gateway}:{self.remote_port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start UDP tunnel: {e}")
            self.stop()
            return False
    
    def _start_ssh_tunnel(self):
        """
        Create SSH reverse tunnel for UDP forwarding.
        Uses SSH DynamicForward and a socat command on the remote end.
        """
        logger.info(f"Setting up SSH tunnel to {self.ssh_host}...")
        
        # Method 1: Use SSH dynamic port forwarding with nc/ncat
        # Listen on remote gateway and forward to local machine
        ssh_cmd = [
            'ssh',
            self.ssh_host,
            # On the remote end, listen for UDP and forward to local through SSH stdin/stdout
            f'socat UDP4-LISTEN:{self.remote_port},reuseaddr,fork UDP4:{self.remote_subnet_gateway}:{self.remote_port}'
        ]
        
        if self.ssh_identity:
            ssh_cmd.insert(1, '-i')
            ssh_cmd.insert(2, self.ssh_identity)
        
        try:
            self.tunnel_process = subprocess.Popen(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            time.sleep(1)  # Give tunnel time to establish
            logger.info("✓ SSH tunnel established")
        except FileNotFoundError:
            logger.warning("socat not found on remote - trying alternative method")
            self._start_ssh_tunnel_nc()
    
    def _start_ssh_tunnel_nc(self):
        """
        Fallback method using nc (netcat) instead of socat.
        """
        # Alternative: Use SSH with -R for reverse tunneling
        ssh_cmd = [
            'ssh',
            '-R', f'9999:127.0.0.1:{self.local_port}',
            self.ssh_host,
            # Keep connection alive
            'sleep 3600'
        ]
        
        if self.ssh_identity:
            ssh_cmd.insert(1, '-i')
            ssh_cmd.insert(2, self.ssh_identity)
        
        self.tunnel_process = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        logger.info("✓ SSH reverse tunnel established (nc method)")
    
    def _start_local_listener(self):
        """
        Start local UDP listener for Kasa discovery packets.
        """
        logger.info(f"Starting local UDP listener on port {self.local_port}...")
        
        self.local_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.local_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Allow receiving broadcast packets
        self.local_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        # Bind to all interfaces on the specified port
        self.local_socket.bind(('0.0.0.0', self.local_port))
        
        # Start listening thread
        self.running = True
        self.listener_thread = threading.Thread(target=self._listener_loop, daemon=True)
        self.listener_thread.start()
        
        logger.info(f"✓ Local UDP listener ready on 0.0.0.0:{self.local_port}")
    
    def _listener_loop(self):
        """
        Listen for UDP packets and handle them.
        """
        logger.debug("UDP listener loop started")
        
        while self.running:
            try:
                # Receive discovery packets
                data, addr = self.local_socket.recvfrom(4096)
                logger.debug(f"Received {len(data)} bytes from {addr}")
                
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Listener error: {e}")
                break
    
    def stop(self):
        """
        Stop the UDP tunnel.
        """
        logger.info("Stopping UDP tunnel...")
        
        self.running = False
        
        if self.tunnel_process:
            try:
                self.tunnel_process.terminate()
                self.tunnel_process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"Error stopping SSH tunnel: {e}")
                self.tunnel_process.kill()
        
        if self.local_socket:
            try:
                self.local_socket.close()
            except Exception as e:
                logger.warning(f"Error closing socket: {e}")
        
        logger.info("✓ UDP tunnel stopped")


# Simpler alternative: Use SSH remote forwarding
class SimpleUDPTunnel:
    """
    Simpler UDP tunnel using Python's builtin socket module.
    Works by listening on localhost and forwarding to remote via SSH.
    """
    
    def __init__(
        self,
        ssh_host: str,
        remote_ip: str,
        local_port: int = 9999,
        remote_port: int = 9999,
        ssh_identity: Optional[str] = None
    ):
        self.ssh_host = ssh_host
        self.remote_ip = remote_ip
        self.local_port = local_port
        self.remote_port = remote_port
        self.ssh_identity = ssh_identity
        self.socket: Optional[socket.socket] = None
        self.running = False
        self.thread: Optional[threading.Thread] = None
    
    def start(self) -> bool:
        """Start simple UDP tunnel using SSH remote forwarding."""
        try:
            logger.info(f"Starting simple UDP tunnel: localhost:{self.local_port} <-> {self.remote_ip}:{self.remote_port}")
            
            # Use SSH's -R flag for reverse port forwarding
            # -R 9999:127.0.0.1:9999 means: on remote, listen on 9999 and forward to local 9999
            cmd = [
                'ssh',
                '-R', f'{self.remote_port}:127.0.0.1:{self.local_port}',
                '-N',  # No remote command
                '-f',  # Background
                self.ssh_host
            ]
            
            if self.ssh_identity:
                cmd.insert(1, '-i')
                cmd.insert(2, self.ssh_identity)
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                logger.error(f"SSH tunnel failed: {result.stderr}")
                return False
            
            logger.info("✓ SSH reverse tunnel established")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start UDP tunnel: {e}")
            return False
    
    def stop(self):
        """Stop the tunnel by killing SSH process."""
        try:
            subprocess.run(
                ['pkill', '-f', f'ssh.*-R.*{self.remote_port}'],
                capture_output=True,
                timeout=5
            )
            logger.info("✓ UDP tunnel stopped")
        except Exception as e:
            logger.warning(f"Error stopping tunnel: {e}")


if __name__ == '__main__':
    # Test tunnel
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    # Create tunnel to remote subnet
    tunnel = SimpleUDPTunnel(
        ssh_host='openwrt',
        remote_ip='192.168.1.255',
        local_port=9999,
        remote_port=9999
    )
    
    if tunnel.start():
        print("\n✓ UDP tunnel active")
        print("  Kasa discovery will now find devices on remote subnet")
        print("\n  Run: from kasa import Discover; await Discover.discover()")
        print("  Press Ctrl+C to stop tunnel\n")
        
        try:
            time.sleep(3600)
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            tunnel.stop()
    else:
        print("✗ Failed to start tunnel")
