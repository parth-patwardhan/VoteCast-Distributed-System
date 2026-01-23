import socket
import json
import uuid
import threading
import signal

from config import MCAST_GRP, MCAST_PORT, BUF


class Client:
    def __init__(self):
        # Own communication
        self.id = str(uuid.uuid4())
        self.__log(f"ID: {self.id}")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(2)

        # Leader server
        self.leader = None
        
        # Authentication
        self.token = None

        # FO reliable multicast R^q_g and FIFO
        self.R = {}
        self.hold_back = {}

        # Shutdown handling
        self.stop_event = threading.Event()
        signal.signal(signal.SIGINT, self.__shutdown)
        signal.signal(signal.SIGTERM, self.__shutdown)

    def __log(self, msg):
        print(f"[CLIENT] {msg}")

    def __shutdown(self, *_):
        self.__log("Shutting down...")
        self.stop_event.set()

    def __send_leader_request(self):
        # Send request to server multicast group
        self.sock.sendto("WHO_IS_LEADER".encode(), (MCAST_GRP, MCAST_PORT))

    def __send(self, msg):
        if self.leader is None:
            self.__log("Error: No leader")

        # Send request to leader server
        ip, port = self.leader.split(":")
        self.sock.sendto(json.dumps(msg).encode(), (ip, int(port)))

    def __recv(self):
        data, _ = self.sock.recvfrom(BUF)
        return json.loads(data.decode())

    def discover_leader(self):
        self.__log("Requesting leader via multicast...")

        # Request leader
        self.__send_leader_request()

        # Wait for reply
        while self.leader is None:
            try:
                data, _ = self.sock.recvfrom(BUF)
                msg = data.decode()
                if msg.startswith("LEADER:"):
                    _, sid = msg.split(":", 1)
                    self.leader = sid
            except socket.timeout:
                self.__send_leader_request()
                continue

        self.__log(f"Leader is {self.leader}")

    def __send_register_request(self):
        self.__send({
            "type": "REGISTER",
            "id": self.id
        })

    def __create_group(self, name):
        self.__send({
            "type": "CREATE_GROUP",
            "id": self.id,
            "token": self.token,
            "group": name
        })

    def register(self):
        self.__log("Registering client...")

        # Request registration
        self.__send_register_request()
        
        # Wait for reply or request again
        while self.token is None:
            try:
                reply = self.__recv()
                token = reply.get("token")
                if token is None:
                    self.__log(f"Error: Expected 'token': {reply}")
                    continue

                self.token = token
                self.__log("Registered successfully")
                        
            except socket.timeout:
                self.__send_register_request()
                continue

    def __get_groups(self):
        self.__send({
            "type": "GET_GROUPS",
            "id": self.id,
            "token": self.token
        })

    def __join_group(self, name):
        self.__send({
            "type": "JOIN_GROUP",
            "group": name,
            "id": self.id,
            "token": self.token
        })

    def __joined_groups(self):
        self.__send({
            "type": "JOINED_GROUPS",
            "id": self.id,
            "token": self.token
        })

    def __leave_group(self, name):
        self.__send({
            "type": "LEAVE_GROUP",
            "group": name,
            "id": self.id,
            "token": self.token
        })

    def __start_vote(self, name, topic, options, timeout):
        self.__send({
            "type": "START_VOTE",
            "group": name,
            "topic": topic,
            "options": options,
            "timeout": timeout,
            "id": self.id,
            "token": self.token
        })

    def __fo_deliver(self, g, q, msg):
        # TODO: save vote so that client can answer in CLI
        S = msg["S"]
        self.R[g][q] = S

    def __send_fo_ack(self, g, q, S, vote):
         ip, port = q.split(":")
         # TODO: self.__send to leader
         self.sock.sendto(json.dumps({
            "type": "VOTE_ACK",
            "group": g,
            "sender": q,
            "seq": S,
            "id": self.id,
            "vote": vote,
            "id": self.id,
            "token": self.token
        }).encode(), (ip, int(port)))

    def __vote(self, msg):
        g = msg["group"]
        # TODO: Do we need sender?
        q = msg["sender"]
        S = msg["S"]
        
        if g not in self.R:
            self.R[g] = {}
            self.hold_back[g] = {}

        if q not in self.R[g]:
            self.R[g][q] = -1
            self.hold_back[g][q] = {}

        R_qg = self.R[g][q]
        
        if S == R_qg + 1:
            self.__fo_deliver(g, q, msg)

            while (self.R[g][q] + 1) in self.hold_back[g][q]:
                next_seq = self.R[g][q] + 1
                buffered = self.hold_back[g][q].pop(next_seq)
                self.__fo_deliver(g, q, buffered)

        elif S > R_qg + 1:
            self.hold_back[g][q][S] = msg

        self.__send_fo_ack(g, q, S, "McDonalds")

    def __handle_message(self, msg, addr):
        t = msg.get("type")
        
        if t == "VOTE":
            self.__vote(msg)
        else:
            self.__log(f"Got message: {msg}")

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
        if self.leader is None:
            self.__log("Error: No leader")

        message_thread = threading.Thread(target=self.__message_handling)
        message_thread.start()

        # TODO: Remove test
        self.__create_group("Test")
        self.__start_vote("Test", "Essen", ["McDonalds", "Burger King"], 30)

        # CLI
        while not self.stop_event.is_set():
            print("\n--- Menu ---")
            print("1) Show leader")
            print("2) Show available groups")
            print("3) Show joined groups")
            print("4) Create group")
            print("5) Join group")
            print("6) Leave group")
            print("7) Start vote")
            print("8) Exit")
            choice = int(input("Choose: "))
            if choice == 1:
                print(f"Leader: {self.leader}")
            elif choice == 2:
                self.__get_groups()
            elif choice == 3:
                self.__joined_groups()
            elif choice == 4:
                name = input("Group name: ")
                self.__create_group(name)
            elif choice == 5:
                name = input("Group name: ")
                self.__join_group(name)
            elif choice == 6:
                name = input("Group name: ")
                self.__leave_group(name)
            elif choice == 7:
                name = input("Group name: ")
                topic = input("Topic: ")
                timeout = int(input("Timeout: "))
                options = []
                stop = False
                i = 0
                while not stop:
                    i += 1
                    option = input(f"Option {i} ('s' to stop): ")
                    if option == "s":
                        stop = True
                    else:
                        options.append(option)
                self.__start_vote(name, topic, options, timeout)
            elif choice == 8:
                return
            else:
                print("Invalid choice")

        # Clean exit
        message_thread.join()


if __name__ == "__main__":
    client = Client()

    # Start leader discovery because this is the server all clients talk to
    client.discover_leader()

    # Get secret token from leader
    client.register()

    # Run client to form groups and start votes
    client.run()
