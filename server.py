import socket
import json
import threading
import time
import signal
import click

MCAST_GRP = "224.1.1.1"
MCAST_PORT = 5007
BUF = 4096


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


class Server:
    def __init__(self, port):
        # Communication socket
        self.ip = get_local_ip()
        self.port = port
        self.id = f"{self.ip}:{self.port}"
        self.__open_client_side_socket()

        # Server-side discovery (HS algorithm)
        self.servers = set([self.id])
        self.left = None
        self.right = None
        self.leader = None
        self.is_leader = False
        self.phase = 0
        self.pending_replies = 0
        self.election_in_progress = False
        self.__open_discovery_socket()

        # Shutdown handling
        self.stop_event = threading.Event()
        signal.signal(signal.SIGINT, self.__shutdown)
        signal.signal(signal.SIGTERM, self.__shutdown)

    def __log(self, msg):
        print(f"[SERVER] {msg}")

    def __open_discovery_socket(self):
        self.__log("Opening discovery service")
        self.mcast = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.mcast.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.mcast.bind(("", MCAST_PORT))
        self.mcast.settimeout(1.0)
        mreq = socket.inet_aton(MCAST_GRP) + socket.inet_aton("0.0.0.0")
        self.mcast.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    
    def __open_client_side_socket(self):
        self.__log("Opening communication socket")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.ip, self.port))
        self.sock.settimeout(1.0)

    def __shutdown(self, *_):
        self.__log("Shutting down...")
        self.stop_event.set()

    def __discovery_service(self):
        while not self.stop_event.is_set():
            try:
                data, _ = self.mcast.recvfrom(1024)
            except socket.timeout:
                continue

            msg = data.decode()
            if msg.startswith("SERVER:"):
                _, sid = msg.split(":", 1)
                if sid not in self.servers:
                    self.__log(f"Discovery service found server: {sid}")
                    self.servers.add(sid)
                    self.__build_ring()
    
    def __discovery_service_broadcast(self, interval=1.0):
        self.__log("Starting continuous discovery broadcast thread")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        msg = f"SERVER:{self.id}".encode()

        while not self.stop_event.is_set():
            try:
                sock.sendto(msg, (MCAST_GRP, MCAST_PORT))
            except Exception as e:
                self.__log(f"Error broadcasting discovery: {e}")

            time.sleep(interval)

        sock.close()

    def __build_ring(self):
        ordered = sorted(self.servers)
        self.__log(f"Ordered ring: {ordered}")

        idx = ordered.index(self.id)
        self.left = ordered[(idx - 1) % len(ordered)]
        self.right = ordered[(idx + 1) % len(ordered)]
        self.__log(f"Created ring left={self.left}, right={self.right}")

    def __send(self, server_id, msg):
        ip, port = server_id.split(":")
        self.sock.sendto(json.dumps(msg).encode(), (ip, int(port)))

    def __hs_start(self):
        if self.election_in_progress:
            self.__log("Election already in progress!")
            return  # avoid starting multiple elections

        if self.left is None or self.right is None:
            self.__build_ring()
            
        self.election_in_progress = True
        self.leader = None
        self.is_leader = False
        self.phase = 0
        self.__log("Starting Hirschberg-Sinclair election...")
        self.__hs_send_neighbors()

    def __hs_send_neighbors(self):
        distance = 2 ** self.phase
        self.pending_replies = 2

        for direction in ("LEFT", "RIGHT"):
            msg = {
                "type": "HS_ELECTION",
                "id": self.id,
                "phase": self.phase,
                "direction": direction,
                "hop": distance
            }
            neighbor = self.left if direction == "LEFT" else self.right
            self.__send(neighbor, msg)

    def __hs_election(self, msg):
        cid = msg.get("id")
        hop = msg.get("hop")
        direction = msg.get("direction")

        if cid is None:
            self.__log(f"Error: Expected key 'id': {msg}")
            return
        
        if hop is None:
            self.__log(f"Error: Expected key 'hop': {msg}")
            return

        if direction is None:
            self.__log(f"Error: Expected key 'direction': {msg}")
            return

        if direction not in ["LEFT", "RIGHT"]:
            self.__log(f"Error: Wrong value of 'direction': {direction}")
            return

        neighbor = self.left if direction == "LEFT" else self.right

        if cid < self.id:
            # TODO: Something is wrong, a server with lower ID cannot start an election because of this
            return
        
        if hop > 1:
            hop -= 1
            self.__send(neighbor, msg)
        else:
            reply = {
                "type": "HS_REPLY",
                "id": cid,
                "direction": msg["direction"]
            }
            self.__send(neighbor, reply)

    def __hs_reply(self, msg):
        cid = msg.get("id")
        direction = msg.get("direction")

        if cid is None:
            self.__log(f"Error: Expected key 'id': {msg}")
            return

        if direction is None:
            self.__log(f"Error: Expected key 'direction': {msg}")
            return

        if direction not in ["LEFT", "RIGHT"]:
            self.__log(f"Error: Wrong value of 'direction': {direction}")
            return

        neighbor = self.left if direction == "LEFT" else self.right

        if cid != self.id:
            if neighbor != self.id:
                self.__send(neighbor, msg)
            return
        
        self.pending_replies -= 1

        if self.pending_replies == 0:
            self.phase += 1
            if 2 ** self.phase >= len(self.servers):
                self.__hs_declare_leader()
            else:
                self.__hs_send_neighbors()

    def __hs_declare_leader(self):
        self.__log("HS: I am the leader")
        msg = {"type": "HS_LEADER", "id": self.id}
        self.leader = self.id
        self.is_leader = True
        self.election_in_progress = False
        self.__send(self.left, msg)

    def __hs_leader(self, msg):
        cid = msg.get("id")
        
        if cid is None:
            self.__log(f"Error: Expected key 'id': {msg}")
            return

        if self.leader is not None:
            return

        self.leader = cid
        self.is_leader = (self.leader == self.id)
        self.election_in_progress = False
        self.__log(f"HS: Leader elected: {self.leader}")

        if self.left != cid:
            self.__send(self.left, msg)

    def __handle_message(self, msg, addr):
        t = msg.get("type")
        if t == "HS_ELECTION":
            self.__log("Got: HS_ELECTION")
            self.__hs_election(msg)
        elif t == "HS_REPLY":
            self.__log("Got: HS_REPLY")
            self.__hs_reply(msg)
        elif t == "HS_LEADER":
            self.__log("Got: HS_LEADER")
            self.__hs_leader(msg)
        else:
            self.__log("Error: Got invalid message")

    def __message_handling(self):
        while not self.stop_event.is_set():
            try:
                data, addr = self.sock.recvfrom(BUF)
                if data:
                    try:
                        msg = json.loads(data.decode())
                        self.__handle_message(msg, addr)
                    except Exception as e:
                        self.__log(f"Invalid message: {e}")
            except socket.timeout:
                continue

    def run(self):
        # Discovery via multicast in other threads
        discovery_thread = threading.Thread(target=self.__discovery_service)
        discovery_thread.start()

        broadcast_thread = threading.Thread(target=self.__discovery_service_broadcast)
        broadcast_thread.start()

        # CLI is in another thread to not interrupt the server
        message_thread = threading.Thread(target=self.__message_handling)
        message_thread.start()

        # CLI
        while not self.stop_event.is_set():
            print("\n--- Menu ---")
            print("1) Show discovered servers")
            print("2) Start HS election")
            print("3) Show leader")
            print("4) Exit")
            choice = int(input("Choose: "))
            if choice == 1:
                print(f"Servers: {sorted(self.servers)}")
            elif choice == 2:
                self.__hs_start()
            elif choice == 3:
                print(f"Leader: {self.leader}")
            elif choice == 4:
                self.stop_event.set()
            else:
                print("Invalid choice")

        # Clean exit
        discovery_thread.join()
        broadcast_thread.join()
        message_thread.join()
        self.sock.close()
        self.mcast.close()
        self.__log("Shutdown")


@click.command()
@click.argument("port")
def main(port):
    port = int(port)
    Server(port).run()
    pass


if __name__ == "__main__":
    main()
