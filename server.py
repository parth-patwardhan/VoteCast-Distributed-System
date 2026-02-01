import socket
import json
import threading
import time
import signal
import click
import secrets
import uuid
from collections import defaultdict

from config import MCAST_GRP, MCAST_PORT, BUF
from ring import build_ring
from discovery import discovery_service, discovery_service_broadcast
from election import hs_start, hs_election, hs_reply, hs_leader
from replication import send_replicate_state, replicate_state_apply
from fo_multicast import fo_multicast, fo_retransmit_loop
import handlers


HEARTBEAT_TIMEOUT = 5.0
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_RESET = "\033[0m"


def color_text(text, color):
    return f"{color}{text}{COLOR_RESET}"


def get_local_ip():
    return "127.0.0.1"


def requires_auth(fn):
    def wrapper(self, msg, addr):
        if not self.is_authenticated(msg):
            self.send_error(addr, "AUTH_FAILED")
            return
        return fn(self, msg, addr)
    return wrapper


class Server:
    def __init__(self, port):
        self.log_queue = []
        self.log_lock = threading.Lock()
        # Communication socket
        self.ip = get_local_ip()
        self.port = port
        self.id = f"{self.ip}:{self.port}"
        self.__open_client_side_socket()

        # Server-side discovery (HS algorithm)
        self.servers = {self.id}
        self.left = None
        self.right = None
        self.leader = None
        self.is_leader = False
        self.phase = 0
        self.pending_replies = 0
        self.election_in_progress = False
        self.election_done = threading.Event()
        self.__open_discovery_socket()

        # Client authentication
        self.clients = {}

        # Vote application
        self.groups = {}
        self.votes = {}

        # FO reliable multicast S^p_g
        self.S = {}
        self.fo_pending = {}

        # Heartbeat state
        self.last_heartbeat_time = time.time()
        self.heartbeat_ack_received = True

        # Server-side fault handling
        self.client_votes = {}  # Track votes per client per vote_id
        self.vote_signatures = {}  # Track vote integrity

        # Server-side vote validation
        self.vote_hashes = {}  # Track vote hashes for integrity

        # Shutdown handling
        self.stop_event = threading.Event()
        signal.signal(signal.SIGINT, self.__shutdown)
        signal.signal(signal.SIGTERM, self.__shutdown)

        # Expose constants/utilities for modules
        self.MCAST_GRP = MCAST_GRP
        self.MCAST_PORT = MCAST_PORT
        self.HEARTBEAT_TIMEOUT = HEARTBEAT_TIMEOUT
        self.COLOR_GREEN = COLOR_GREEN
        self.COLOR_YELLOW = COLOR_YELLOW
        self.COLOR_RED = COLOR_RED
        self.COLOR_RESET = COLOR_RESET
        self.color_text = color_text

    def __print_menu(self):
        print("\n--- Menu ---")
        print("1) Show discovered servers")
        print("2) Start HS election")
        print("3) Show leader")
        print("4) Exit")

    # Public helpers for modules
    def log(self, msg):
        return self.__log(msg)

    def leader_send(self, server_id, msg):
        return self.__leader_send(server_id, msg)

    def send_replicate_state(self, new_leader):
        return send_replicate_state(self, new_leader)

    def is_authenticated(self, msg):
        cid = msg.get("id")
        token = msg.get("token")
        return cid in self.clients and self.clients[cid]["token"] == token

    def send_error(self, addr, err):
        self.send(addr, {"type": "ERROR", "error": err})

    def __log(self, msg):
        with self.log_lock:
            self.log_queue.append(f"[SERVER] {msg}")

    def __flush_logs(self):
        with self.log_lock:
            if not self.log_queue:
                return
            for entry in self.log_queue:
                print(entry)
            self.log_queue.clear()

    def __log_flush_loop(self):
        while not self.stop_event.is_set():
            self.__flush_logs()
            time.sleep(0.2)

    def __open_discovery_socket(self):
        self.mcast = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.mcast.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.mcast.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.mcast.bind(("", MCAST_PORT))
        self.mcast.settimeout(1.0)
        mreq = socket.inet_aton(MCAST_GRP) + socket.inet_aton("0.0.0.0")
        self.mcast.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.mcast.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    
    def __open_client_side_socket(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.ip, self.port))
        self.sock.settimeout(1.0)

    def __shutdown(self, *_):
        self.stop_event.set()

    # discovery moved to discovery.py
    
    # discovery broadcast moved to discovery.py

    def send_heartbeat(self):
        if self.left is None or self.left == self.id:
            self.last_heartbeat_time = time.time()
            self.heartbeat_ack_received = True
            return
        self.send(self.left, {"type": "HEARTBEAT", "id": self.id})

    # build_ring moved to ring.py

    def send(self, server_id, msg):
        if not server_id:
            self.__log(f"Error: send called with empty server_id for msg {msg}")
            return
        if type(server_id) is not tuple:
            ip, port = server_id.split(":")
            self.sock.sendto(json.dumps(msg).encode(), (ip, int(port)))
        else:
            self.sock.sendto(json.dumps(msg).encode(), server_id)

    def __leader_send(self, server_id, msg):
        """
        This is for the communication with the client.
        Only the leader communicates with the client.
        """
        if not self.is_leader:
            return
        
        if type(server_id) is not tuple:
            ip, port = server_id.split(":")
            self.sock.sendto(json.dumps(msg).encode(), (ip, int(port)))
        else:
            self.sock.sendto(json.dumps(msg).encode(), server_id)

    # HS election moved to election.py

    def __register(self, msg, addr):
        handlers.register(self, msg, addr)

    @requires_auth
    def __create_group(self, msg, addr):
        handlers.create_group(self, msg, addr)

    @requires_auth
    def __get_groups(self, msg, addr):
        handlers.get_groups(self, msg, addr)

    @requires_auth
    def __join_group(self, msg, addr):
        handlers.join_group(self, msg, addr)

    @requires_auth
    def __joined_groups(self, msg, addr):
        handlers.joined_groups(self, msg, addr)

    @requires_auth
    def __leave_group(self, msg, addr):
        handlers.leave_group(self, msg, addr)

    @requires_auth
    def __start_vote(self, msg, addr):
        handlers.start_vote(self, msg, addr)

    @requires_auth
    def __vote_ack(self, msg, addr):
        handlers.vote_ack(self, msg, addr)

    def __handle_message(self, msg, addr):
        t = msg.get("type")
        if t == "HS_ELECTION":
            hs_election(self, msg)
        elif t == "HS_REPLY":
            hs_reply(self, msg)
        elif t == "HS_LEADER":
            hs_leader(self, msg)
        elif t == "REGISTER":
            self.__log("Got: REGISTER")
            self.__register(msg, addr)
        elif t == "CREATE_GROUP":
            self.__log("Got: CREATE_GROUP")
            self.__create_group(msg, addr)
        elif t == "GET_GROUPS":
            self.__log("Got: GET_GROUPS")
            self.__get_groups(msg, addr)
        elif t == "JOIN_GROUP":
            self.__log("Got: JOIN_GROUP")
            self.__join_group(msg, addr)
        elif t == "JOINED_GROUPS":
            self.__log("Got: JOINED_GROUPS")
            self.__joined_groups(msg, addr)
        elif t == "LEAVE_GROUP":
            self.__log("Got: LEAVE_GROUP")
            self.__leave_group(msg, addr)
        elif t == "START_VOTE":
            self.__log("Got: START_VOTE")
            self.__start_vote(msg, addr)
        elif t == "VOTE_ACK":
            self.__log("Got: VOTE_ACK")
            self.__vote_ack(msg, addr)
        elif t == "REPL_REGISTER":
            self.__log("Got: REPL_REGISTER")
            cid = msg["id"]
            token = msg["token"]
            addr = tuple(msg["addr"])

            # Replicate clients
            self.clients[cid] = {"token": token, "addr": addr}
        elif t == "REPL_VOTE":
            self.__log("Got: REPL_VOTE")
            vote_id = msg.get("vote_id")
            group = msg.get("group")
            topic = msg.get("topic")
            options = msg.get("options")
            timeout = msg.get("timeout")
            votes = msg.get("votes", [])

            # Replicate the vote in local state
            self.votes[vote_id] = {
                "group": group,
                "topic": topic,
                "options": options,
                "votes": votes
            }

            # Now add this vote to the pending list for FO multicast
            if group not in self.S:
                self.S[group]

            # Create an entry in the pending queue for this vote
            self.fo_pending[(group, self.S[group])] = {
                "pending": set(self.groups[group]["members"]),
                "deadline": time.time() + timeout,
                "msg": {
                    "type": "VOTE",
                    "vote_id": vote_id,
                    "group": group,
                    "topic": topic,
                    "options": options
                },
                "vote_id": vote_id
            }

            # Increment the sequence number after adding it to pending
            self.S[group] += 1
        elif t == "REPL_STATE":
            self.__log("Got: REPL_STATE")
            replicate_state_apply(self, msg)
        elif t == "HEARTBEAT":
            # self.__log("Got: HEARTBEAT")
            self.send(addr, {"type": "HEARTBEAT_ACK", "id": self.id})
        elif t == "HEARTBEAT_ACK":
            # self.__log("Got: HEARTBEAT_ACK")
            sender_id = msg.get("id")
            if sender_id == self.left:
                # Ackknowledge heartbeat
                self.last_heartbeat_time = time.time()
                self.heartbeat_ack_received = True
        else:
            self.__log(f"Error: Got invalid message: {msg}")

    def message_handling(self):
        while not self.stop_event.is_set():
            try:
                data, addr = self.sock.recvfrom(BUF)
                if data:
                    try:
                        msg = json.loads(data.decode())
                        self.__handle_message(msg, addr)
                    except Exception as e:
                        self.__log(f"Invalid message: {e}")
            except:
                continue

    def run(self):
        # Discovery via multicast in other threads
        discovery_thread = threading.Thread(target=discovery_service, args=(self,))
        discovery_thread.start()

        broadcast_thread = threading.Thread(target=discovery_service_broadcast, args=(self,))
        broadcast_thread.start()

        # CLI is in another thread to not interrupt the server
        message_thread = threading.Thread(target=self.message_handling)
        message_thread.start()

        # FO multicast thread
        retransmit_thread = threading.Thread(target=fo_retransmit_loop, args=(self,))
        retransmit_thread.start()

        log_thread = threading.Thread(target=self.__log_flush_loop)
        log_thread.start()

        # CLI
        while not self.stop_event.is_set():
            self.__flush_logs()
            self.__print_menu()
            raw_choice = input("Choose: ").strip()
            if not raw_choice:
                continue
            choice = int(raw_choice)
            if choice == 1:
                print(color_text(f"Servers: {sorted(self.servers)}", COLOR_GREEN))
                print()
            elif choice == 2:
                hs_start(self, manual=True)
                print()
            elif choice == 3:
                print(color_text(f"Leader: {self.leader}", COLOR_GREEN))
                print()
            elif choice == 4:
                self.stop_event.set()
            else:
                print("Invalid choice")

        # Clean exit
        discovery_thread.join()
        broadcast_thread.join()
        message_thread.join()
        retransmit_thread.join()
        log_thread.join()
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
