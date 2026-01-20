import socket
import json
import threading
import time
import sys
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

        # Server-side discovery
        self.servers = set([self.id])
        self.leader = None
        self.is_leader = False
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

    def discovery_listener(self):
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

    def elect_leader(self):
        self.__log(f"Running leader election...")
        self.leader = max(self.servers)
        self.is_leader = (self.leader == self.id)
        self.__log(f"Elected leader is {self.leader} ({'me' if self.is_leader else 'not me'})")

    def handle_message(self, msg, addr):
        self.__log(f"Received message: {msg} from {addr}")

    def run(self):
        # Discovery via multicast in another thread
        discovery_thread = threading.Thread(target=self.discovery_listener)
        discovery_thread.start()

        # Start leader election
        time.sleep(1)
        self.elect_leader()

        # Incoming message handling
        while not self.stop_event.is_set():
            try:
                data, addr = self.sock.recvfrom(BUF)
            except socket.timeout:
                continue

            msg = json.loads(data.decode())
            self.handle_message(msg, addr)

        # Clean exit
        discovery_thread.join()
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