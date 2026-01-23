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


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def requires_auth(fn):
    def wrapper(self, msg, addr):
        if not self.is_authenticated(msg):
            self.send_error(addr, "AUTH_FAILED")
            return
        return fn(self, msg, addr)
    return wrapper


class Server:
    def __init__(self, port):
        # Communication socket
        self.ip = get_local_ip()
        self.port = port
        self.id = f"{self.ip}:{self.port}"
        self.__open_client_side_socket()

        # Server-side discovery (HS algorithm)
        self.servers = set()
        self.left = None
        self.right = None
        self.leader = None
        self.is_leader = False
        self.phase = 0
        self.pending_replies = 0
        self.election_in_progress = False
        self.__open_discovery_socket()

        # Client authentication
        self.clients = {}

        # Vote application
        self.groups = {}
        self.votes = {}

        # FO reliable multicast S^p_g
        self.S = {}
        self.fo_pending = {}

        # Shutdown handling
        self.stop_event = threading.Event()
        signal.signal(signal.SIGINT, self.__shutdown)
        signal.signal(signal.SIGTERM, self.__shutdown)

    def is_authenticated(self, msg):
        cid = msg.get("id")
        token = msg.get("token")
        return cid in self.clients and self.clients[cid]["token"] == token

    def send_error(self, addr, err):
        self.__send(addr, {"type": "ERROR", "error": err})

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
                data, addr = self.mcast.recvfrom(1024)
                msg = data.decode()
                if msg.startswith("SERVER:"):
                    _, sid = msg.split(":", 1)
                    if sid not in self.servers:
                        self.__log(f"Discovery service found server: {sid}")
                        self.servers.add(sid)
                        self.__build_ring()

                        # If the server joined itself, start HS
                        if self.id == sid:
                            time.sleep(2)  # Needed with >1s so that other servers can discover it
                            self.__hs_start()
                elif msg == "WHO_IS_LEADER":
                        self.__log(f"Discovery service got leader request")
                        if self.is_leader:
                            self.sock.sendto(f"LEADER:{self.id}".encode(), addr)
                            self.__log("Replied to leader request")
            except socket.timeout:
                continue
    
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
            # Swallow message from lower IDs or start own election
            if not self.election_in_progress:
                self.__hs_start()
            return
        
        if hop > 1:
            msg["hop"] -= 1
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
        self.leader = self.id
        self.is_leader = True
        self.election_in_progress = False
        msg = {"type": "HS_LEADER", "id": self.id}
        self.__send(self.left, msg)

    def __hs_leader(self, msg):
        cid = msg.get("id")
        
        if cid is None:
            self.__log(f"Error: Expected key 'id': {msg}")
            return

        self.leader = cid
        self.is_leader = (self.leader == self.id)
        self.election_in_progress = False
        self.__log(f"HS: Leader elected: {self.leader}")

        if self.left != cid:
            self.__send(self.left, msg)

    def __register(self, msg, addr):
        cid = msg.get("id")

        if cid is None:
            self.__log(f"Error: Expected key 'id': {msg}")
            return

        token = secrets.token_hex(16)
        self.clients[cid] = {
            "token": token,
            "addr": addr
        }

        # Replicate to other servers
        if self.is_leader:
            for server in self.servers:
                if server != self.id:
                    self.__leader_send(server, {
                        "type": "REPL_REGISTER",
                        "id": cid,
                        "token": token,
                        "addr": addr
                    })

        self.__leader_send(addr, {"type": "REGISTER_OK", "token": token})

    @requires_auth
    def __create_group(self, msg, addr):
        cid = msg.get("id")
        name = msg.get("group")

        if cid is None:
            self.__log(f"Error: Expected key 'id': {msg}")
            return

        if name is None:
            self.__log(f"Error: Expected key 'group': {msg}")
            return

        if name in self.groups:
            self.__log(f"Error: Group already exists: {name}")
            return

        # Create group
        self.groups[name] = {
            "owner": cid,
            "members": {cid}
        }

        # Initialize sequence counter for group
        self.S[name] = 0
        
        self.__leader_send(addr, {"type": "CREATE_GROUP_OK", "group": name})

    @requires_auth
    def __get_groups(self, msg, addr):
        groups = [g for g in self.groups.keys()]
        self.__leader_send(addr, {"type": "GET_GROUPS_OK", "groups": groups})

    @requires_auth
    def __join_group(self, msg, addr):
        cid = msg.get("id")
        name = msg.get("group")

        if cid is None:
            self.__log(f"Error: Expected key 'id': {msg}")
            return

        if name is None:
            self.__log(f"Error: Expected key 'group': {msg}")
            return

        if name not in self.groups:
            self.__log(f"Error: Group does not exist: {name}")
            return

        self.groups[name]["members"].add(cid)
        self.__leader_send(addr, {"type": "JOIN_GROUP_OK", "group": name})

    @requires_auth
    def __joined_groups(self, msg, addr):
        cid = msg.get("id")

        if cid is None:
            self.__log(f"Error: Expected key 'id': {msg}")
            return

        groups = [g for g in self.groups.keys() if cid in self.groups[g]["members"]]
        self.__leader_send(addr, {"type": "JOINED_GROUPS_OK", "groups": groups})

    @requires_auth
    def __leave_group(self, msg, addr):
        cid = msg.get("id")
        name = msg.get("group")

        if cid is None:
            self.__log(f"Error: Expected key 'id': {msg}")
            return

        if name is None:
            self.__log(f"Error: Expected key 'group': {msg}")
            return

        if name not in self.groups:
            self.__log(f"Error: Group does not exist: {name}")
            return

        if cid not in self.groups[name]["members"]:
            self.__log(f"Error: Not a member in group {name}")
            return

        self.groups[name]["members"].remove(cid)
        self.__leader_send(addr, {"type": "LEAVE_GROUP_OK", "group": name})

    def __fo_multicast(self, group, payload, timeout):
        """
        FO-multicast(g, m):
        - piggyback S_pg
        - increment S_pg
        - B-multicast
        """
        seq = self.S[group]

        msg = {
            "S": seq,
            "sender": self.id,
            **payload
        }

        pending = set(self.groups[group]["members"])

        # Buffer pending requests
        self.fo_pending[(group, seq)] = {
            "pending": set(self.groups[group]["members"]),
            "deadline": time.time() + timeout,
            "msg": msg,
            "vote_id": payload["vote_id"]
        }

        # Increment S_pg
        self.S[group] += 1

        # B-multicast
        for cid in pending:
            self.__leader_send(self.clients[cid]["addr"], msg)

    @requires_auth
    def __start_vote(self, msg, addr):
        cid = msg.get("id")
        name = msg.get("group")
        topic = msg.get("topic")
        options = msg.get("options")
        timeout = msg.get("timeout")

        if cid is None:
            self.__log(f"Error: Expected key 'id': {msg}")
            return

        if options is None:
            self.__log(f"Error: Expected key 'options': {msg}")
            return

        if timeout is None:
            self.__log(f"Error: Expected key 'timeout': {msg}")
            return

        if topic is None:
            self.__log(f"Error: Expected key 'timeout': {msg}")
            return

        if name is None:
            self.__log(f"Error: Expected key 'group': {msg}")
            return

        if name not in self.groups:
            self.__log(f"Error: Group does not exist: {name}")
            return

        if cid not in self.groups[name]["members"]:
            self.__log(f"Error: Not a member in group {name}")
            return

        self.__leader_send(addr, {"type": "START_VOTE_OK", "group": name, "topic": topic, "options": options, "timeout": timeout})

        vote_id = str(uuid.uuid4())

        # Create entry for the vote
        self.votes[vote_id] = {
            "group": name,
            "topic": topic,
            "options": options,
            "votes": []
        }

        # FO reliable multicast it to the group
        payload = {
            "type": "VOTE",
            "vote_id": vote_id,
            "group": name,
            "topic": topic,
            "options": options
        }
        self.__fo_multicast(name, payload, timeout)

    @requires_auth
    def __vote_ack(self, msg, addr):
        vote_id = msg.get("vote_id")
        group = msg.get("group")
        sender_seq = msg.get("S")

        if not vote_id or not group or sender_seq is None:
            self.__log(f"Error in VOTE_ACK: Missing vote_id, group, or S")
            return

        # Find the pending FO multicast entry for that sequence
        fo_entry = self.fo_pending.get((group, sender_seq))
        if not fo_entry:
            self.__log(f"Out-of-order or unknown VOTE_ACK for {group}, seq={sender_seq}")
            return

        # Remove sender from pending
        sender_id = msg.get("id")
        if sender_id in fo_entry["pending"]:
            fo_entry["pending"].remove(sender_id)

        # Record the vote
        self.votes[vote_id]["votes"].append(msg)
        self.__log(f"Vote Acknowledged: {msg}")

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
            self.clients[cid] = {"token": token, "addr": addr}
        else:
            self.__log(f"Error: Got invalid message: {msg}")

        # Leader multicasts all incoming requests to 
        # non leader servers so that they can continue
        # in the case he fails / crashes.
        if self.is_leader and t not in ["HS_ELECTION", "HS_REPLY", "HS_LEADER", "REGISTER"]:
            for server in self.servers:
                if server != self.id:
                    self.__leader_send(server, msg)

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

    def __finalize_vote(self, vote_id):
        self.__log(f"Finalizing vote {vote_id}")

        vote = self.votes.get(vote_id)
        if not vote:
            self.__log(f"Vote {vote_id} not found")
            return

        # Select winner
        winner = "No votes, no winner"
        if len(vote["votes"]) > 0:
            vote_counts = defaultdict(int)
            for vote_entry in vote["votes"]:
                vote_counts[vote_entry["vote"]] += 1

            winner = max(vote_counts, key=vote_counts.get)

        # Announce the result via group multicast
        result_msg = {
            "type": "VOTE_RESULT",
            "vote_id": vote_id,
            "group": vote["group"],
            "topic": vote["topic"],
            "winner": winner
        }

        for cid in self.groups[vote["group"]]["members"]:
            self.__leader_send(self.clients[cid]["addr"], result_msg)

    def __fo_retransmit_loop(self):
        while not self.stop_event.is_set():
            now = time.time()
            finished = []

            for key, entry in list(self.fo_pending.items()):
                group, seq = key

                if now > entry["deadline"] or not entry["pending"]:
                    finished.append(key)
                    continue

                for cid in entry["pending"]:
                    self.__leader_send(self.clients[cid]["addr"], entry["msg"])

            for key in finished:
                group, seq = key
                entry = self.fo_pending.pop(key)

                vote_id = entry.get("vote_id")
                if vote_id:
                    self.__finalize_vote(vote_id)

                self.__log(f"FO multicast completed: {group}, seq={seq}")

            time.sleep(0.5)

    def run(self):
        # Discovery via multicast in other threads
        discovery_thread = threading.Thread(target=self.__discovery_service)
        discovery_thread.start()

        broadcast_thread = threading.Thread(target=self.__discovery_service_broadcast)
        broadcast_thread.start()

        # CLI is in another thread to not interrupt the server
        message_thread = threading.Thread(target=self.__message_handling)
        message_thread.start()

        # FO multicast thread
        retransmit_thread = threading.Thread(target=self.__fo_retransmit_loop)
        retransmit_thread.start()

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
        retransmit_thread.join()
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
