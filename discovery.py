import socket
import time
import struct
from ring import build_ring
from election import hs_start

def discovery_service(server):
    """
    Listen for discovery messages from other servers on multicast group.
    Properly configured for macOS multicast loopback.
    """
    # If this is the first/only server in view at startup, declare self as leader immediately.
    if len(server.servers) <= 1:
        server.leader = server.id
        server.is_leader = True
        server.log(server.color_text(f"HS: Leader elected: {server.leader}", server.COLOR_GREEN))
    
    server.log(f"🔍 Discovery service listening on {server.MCAST_GRP}:{server.MCAST_PORT}")
    server.log(f"📍 Bound to interface: {server.ip}")
    
    received_count = 0
    
    while not server.stop_event.is_set():
        try:
            data, addr = server.mcast.recvfrom(1024)
            msg = data.decode()
            
            received_count += 1
            
            # DEBUG: Log first 10 messages to verify reception
            if received_count <= 10:
                server.log(f"📥 [{received_count}] Received: '{msg[:50]}...' from {addr}")
            
            if msg.startswith("SERVER:"):
                _, sid = msg.split(":", 1)
                if not sid:
                    continue
                
                # Always ensure self ID is in the view
                if server.id not in server.servers:
                    server.servers.add(server.id)
                
                # Don't add ourselves
                if sid == server.id:
                    # server.log(f"Ignoring our own broadcast")
                    continue
                
                if sid not in server.servers:
                    server.log(server.color_text(f"✅ Server joined: {sid}", server.COLOR_YELLOW))
                    server.servers.add(sid)
                    server.log(f"📊 Total servers now: {len(server.servers)} - {sorted(list(server.servers))}")
                    build_ring(server)
                    
                    # Auto-start HS when we have more than one server and ring is ready
                    if (
                        not server.election_in_progress
                        and len(server.servers) > 1
                        and server.left is not None
                        and server.right is not None
                    ):
                        server.log("🗳️ Triggering HS election...")
                        hs_start(server)
                    
            elif msg == "WHO_IS_LEADER":
                server.log("Discovery service got leader request")
                if server.is_leader:
                    server.sock.sendto(f"LEADER:{server.id}".encode(), addr)
                    server.log("Replied to leader request")
                    
            elif msg.startswith("CRASH:"):
                # Server left/crashed
                _, sid = msg.split(":", 1)
                # Guard against removing self; ignore if sid is self
                if sid != server.id and sid in server.servers:
                    server.servers.remove(sid)
                    server.log(server.color_text(f"💀 Server left: {sid}", server.COLOR_RED))
                if server.id not in server.servers:
                    server.servers.add(server.id)
                build_ring(server)
                
        except socket.timeout:
            continue
        except Exception as e:
            if not server.stop_event.is_set():
                server.log(f"❌ Discovery error: {e}")
    
    server.log(f"Discovery service stopped (received {received_count} total messages)")


def discovery_service_broadcast(server, interval=1.0):
    """
    Continuously broadcast our own DISCOVERY message to the multicast group.
    Properly configured for macOS multicast with loopback enabled.
    """
    server.log("📢 Starting continuous discovery broadcast thread")
    
    # Create a separate socket for sending multicast
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    
    # CRITICAL: Enable multicast loopback so processes on same machine can see each other
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    server.log("✓ Multicast loopback ENABLED")
    
    # Set TTL for multicast packets (2 = local network)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    server.log("✓ Multicast TTL set to 2")
    
    # CRITICAL: Bind the sending socket to the specific interface
    # This ensures multicast goes out the correct network interface
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, 
                       socket.inet_aton(server.ip))
        server.log(f"✓ Multicast interface bound to {server.ip}")
    except Exception as e:
        server.log(f"⚠️ Warning: Could not bind multicast interface: {e}")
    
    msg = f"SERVER:{server.id}".encode()
    
    server.log(f"📤 Will broadcast to {server.MCAST_GRP}:{server.MCAST_PORT}")
    server.log(f"📝 Message content: SERVER:{server.id}")
    
    # Initial burst of announcements (5 times with short delay)
    server.log("🚀 Sending initial announcement burst...")
    for i in range(5):
        try:
            sock.sendto(msg, (server.MCAST_GRP, server.MCAST_PORT))
            server.log(f"📡 Initial broadcast {i+1}/5: SERVER:{server.id}")
        except Exception as e:
            server.log(f"❌ Initial broadcast error: {e}")
        time.sleep(0.3)
    
    server.log("✓ Initial burst complete, starting periodic broadcasts...")
    
    broadcast_count = 0
    
    while not server.stop_event.is_set():
        # Broadcast for discovery
        try:
            sock.sendto(msg, (server.MCAST_GRP, server.MCAST_PORT))
            broadcast_count += 1
            
            # Log every 10th broadcast
            if broadcast_count % 10 == 0:
                server.log(f"📡 Broadcast #{broadcast_count}: SERVER:{server.id}")
                
        except Exception as e:
            server.log(f"❌ Error broadcasting discovery: {e}")
        
        # Heartbeat check
        current_time = time.time()
        if current_time - server.last_heartbeat_time > server.HEARTBEAT_TIMEOUT:
            if server.heartbeat_ack_received:
                server.heartbeat_ack_received = False
                server.log(f"💔 Heartbeat timeout for {server.left}, assuming crash.")
                try:
                    sock.sendto(f"CRASH:{server.left}".encode(), (server.MCAST_GRP, server.MCAST_PORT))
                except Exception as e:
                    server.log(f"❌ Error broadcasting crash: {e}")
                time.sleep(2)
                # Start new HS to get a new leader
                hs_start(server)
        
        server.send_heartbeat()
        time.sleep(interval)
    
    sock.close()
    server.log(f"📢 Discovery broadcast stopped (sent {broadcast_count} total messages)")