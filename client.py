import socket
import json
import uuid

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

    def __log(self, msg):
        print(f"[CLIENT] {msg}")

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
        self.__send({ "type": "GET_GROUPS" })

    def run(self):
        if self.leader is None:
            self.__log("Error: No leader")

        # CLI
        while True:
            print("\n--- Menu ---")
            print("1) Show leader")
            print("2) Show available groups")
            print("3) Show joined groups")
            print("4) Create group")
            print("5) Start vote")
            print("6) Exit")
            choice = int(input("Choose: "))
            if choice == 1:
                print(f"Leader: {self.leader}")
            elif choice == 2:
                self.__get_groups()
            elif choice == 3:
                pass
            elif choice == 4:
                return
            elif choice == 5:
                return
            elif choice == 6:
                return
            else:
                print("Invalid choice")


if __name__ == "__main__":
    client = Client()

    # Start leader discovery because this is the server all clients talk to
    client.discover_leader()

    # Get secret token from leader
    client.register()

    # Run client to form groups and start votes
    client.run()
