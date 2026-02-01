# 🗳️ VoteCast

**A Fault-Tolerant Distributed Voting System with Leader Election**

VoteCast is a distributed multi-client polling platform that demonstrates advanced distributed systems concepts including leader election, fault tolerance, state replication, and reliable multicast communication.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [System Architecture](#system-architecture)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Testing Fault Tolerance](#testing-fault-tolerance)
- [Technical Details](#technical-details)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

---

## 🎯 Overview

VoteCast enables multiple clients to form poll groups and conduct distributed votes with strong consistency guarantees. The system automatically handles:

- **Leader Election** using the Hirschberg-Sinclair algorithm
- **Fault Detection** via heartbeat monitoring
- **State Replication** for leader failover
- **FIFO-Ordered Reliable Multicast** for vote delivery
- **Automatic Re-election** when the leader crashes

---

## ✨ Features

### Core Features
- ✅ **Distributed Leader Election** - Hirschberg-Sinclair algorithm ensures highest ID wins
- ✅ **Service Discovery** - Automatic peer discovery via multicast
- ✅ **Fault Tolerance** - Heartbeat-based failure detection and automatic re-election
- ✅ **State Replication** - Leader state automatically replicated to backup servers
- ✅ **Group Management** - Create, join, and leave poll groups
- ✅ **Reliable Voting** - FO-multicast ensures FIFO delivery guarantees
- ✅ **Client Authentication** - Secure token-based authentication
- ✅ **Vote Integrity** - Server-side duplicate prevention and validation

### Advanced Features
- 🔄 **Automatic Retransmission** - Votes are retransmitted until acknowledged
- 🎲 **Deterministic Tie-Breaking** - Uses option list order for consistent results
- 🔍 **Real-time Monitoring** - Colored console output for system events
- ⚡ **Concurrent Operations** - Multi-threaded architecture for high performance

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     VoteCast System                      │
├─────────────────────────────────────────────────────────┤
│  Layer 1: Service Discovery (Multicast)                 │
│           └─ Automatic peer detection                   │
│                                                          │
│  Layer 2: Leader Election (Hirschberg-Sinclair)        │
│           └─ Ring topology, highest ID wins             │
│                                                          │
│  Layer 3: State Replication                             │
│           └─ Leader → Backup synchronization            │
│                                                          │
│  Layer 4: Group Management                               │
│           └─ Create/join/leave groups                   │
│                                                          │
│  Layer 5: FO-Reliable Multicast Voting                  │
│           └─ FIFO ordering, retransmission              │
└─────────────────────────────────────────────────────────┘
```

### Component Interaction

```
┌─────────┐      Discovery      ┌─────────┐      Discovery      ┌─────────┐
│Server 1 │◄────────────────────►│Server 2 │◄────────────────────►│Server 3 │
│Port 6001│     (Multicast)      │Port 6002│     (Multicast)      │Port 6003│
└────┬────┘                      └────┬────┘                      └────┬────┘
     │                                │                                │
     │         Hirschberg-Sinclair Election                           │
     │              (Ring Topology)                                    │
     │◄──────────────────────────────────────────────────────────────►│
     │                                                                 │
     │                    Leader Elected (6003 wins)                  │
     │                                                                 │
     ▼                                                                 ▼
┌─────────┐                                                     ┌──────────┐
│ Client 1│──────────── Register/Vote ────────────────────────►│  LEADER  │
└─────────┘                                                     │ (6003)   │
┌─────────┐                                                     │          │
│ Client 2│──────────── Register/Vote ────────────────────────►│ Handles  │
└─────────┘                                                     │ All      │
                                                                │ Clients  │
                                                                └──────────┘
```

---

## 📦 Installation

### Prerequisites
- Python 3.11+
- pip
- Virtual environment (recommended)

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/VoteCast.git
   cd VoteCast
   ```

2. **Create virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

---

## 🚀 Quick Start

### Start Multiple Servers

Open 3 separate terminals and run:

**Terminal 1:**
```bash
cd VoteCast && source venv/bin/activate && python server.py 6001
```

**Terminal 2:**
```bash
cd VoteCast && source venv/bin/activate && python server.py 6002
```

**Terminal 3:**
```bash
cd VoteCast && source venv/bin/activate && python server.py 6003
```

You'll see the servers discover each other and elect a leader (127.0.0.1:6003 will win).

### Start Clients

**Terminal 4:**
```bash
cd VoteCast && source venv/bin/activate && python client.py
```

**Terminal 5:**
```bash
cd VoteCast && source venv/bin/activate && python client.py
```

### Create a Vote

**On Client 1:**
1. Select `4` → Create group → Enter `tech-team`
2. Select `7` → Start vote
   - Group: `tech-team`
   - Topic: `Which framework?`
   - Timeout: `30` seconds
   - Options: `React`, `Vue`, `Angular` (press `s` to stop)

**On Client 2:**
1. Select `5` → Join group → Enter `tech-team`
2. Select `8` → Vote → Choose your option

After the timeout or when all members vote, results are announced!

---

## 🔧 How It Works

### 1. Service Discovery

Servers use **multicast** to announce their presence:
- Each server broadcasts `SERVER:<ip:port>` every 1 second
- All servers listen on multicast group `224.1.1.1:5007`
- When a new server is discovered, the ring topology is rebuilt

### 2. Leader Election (Hirschberg-Sinclair Algorithm)

```python
Phase 0: distance = 2^0 = 1 hop
Phase 1: distance = 2^1 = 2 hops
Phase k: distance = 2^k hops
```

**Algorithm Steps:**
1. Each server sends election messages in both directions (LEFT/RIGHT)
2. Messages travel up to `2^phase` hops
3. If a server receives a message from a higher ID, it swallows it
4. If a server receives replies from both directions, it advances to the next phase
5. When `2^(phase+1) >= total_servers`, the server declares victory

**Complexity:**
- Messages: O(n log n)
- Time: O(log n) phases
- Result: **Highest ID wins**

### 3. Ring Topology

Servers form a **sorted circular ring**:
```
Server A (6001) ← → Server B (6002) ← → Server C (6003)
     ↑                                         ↓
     └─────────────────────────────────────────┘
```

Each server knows its `left` and `right` neighbors for the election.

### 4. Failure Detection

**Heartbeat Protocol:**
- Each server sends `HEARTBEAT` to its left neighbor every 1 second
- If no `HEARTBEAT_ACK` within 5 seconds → **crash detected**
- Crashed server removed from ring
- **New election triggered automatically**

### 5. State Replication

When a new leader is elected:
1. Old leader (if different) sends `REPL_STATE` message
2. New leader receives complete state:
   - Registered clients
   - Active groups
   - Ongoing votes
   - Sequence numbers
3. New leader notifies all clients (`NEW_LEADER` message)

### 6. FO-Reliable Multicast

**FIFO-Ordered Multicast** ensures votes are delivered in order:

```python
# Sender (Leader)
S^p_g = sequence number for group g
Send(msg, S^p_g)
S^p_g += 1

# Receiver (Client)
R^q_g = expected sequence from sender q
if msg.S == R^q_g + 1:
    deliver(msg)
    R^q_g += 1
else:
    buffer(msg)  # Out-of-order, hold back
```

**Reliability:**
- Leader retransmits every 0.5 seconds until ACK received
- Clients ACK each vote
- Timeout ensures eventual completion

### 7. Vote Integrity

**Server-Side Protections:**
- ✅ **Duplicate Prevention**: Only first vote from each client counts
- ✅ **Vote Validation**: Vote must be in the options list
- ✅ **Tie-Breaking**: Deterministic (first option in list wins)

---

## 🧪 Testing Fault Tolerance

### Test 1: Leader Crash

1. Start 3 servers (6001, 6002, 6003)
2. Wait for leader election (6003 wins)
3. **Kill the leader** (Ctrl+C on server 6003)
4. Watch automatic re-election:
   ```
   [SERVER] 💔 Heartbeat timeout for 127.0.0.1:6003
   [SERVER] 💀 Server left: 127.0.0.1:6003
   [SERVER] 🗳️ Triggering HS election...
   [SERVER] HS: Leader elected: 127.0.0.1:6002
   ```
5. Clients automatically switch to new leader

### Test 2: Network Partition

1. Start 3 servers
2. Kill the middle server (6002)
3. Ring rebuilds: 6001 ← → 6003
4. System continues operating

### Test 3: Concurrent Votes

1. Start multiple clients in same group
2. Have them all vote simultaneously
3. Verify FIFO ordering and no duplicates

---

## 📚 Technical Details

### Technologies Used
- **Language**: Python 3.11
- **Networking**: UDP Sockets, Multicast
- **Concurrency**: Threading
- **CLI**: Click framework
- **Authentication**: Secure token generation

### Configuration

Edit `config.py`:
```python
MCAST_GRP = "224.1.1.1"  # Multicast group address
MCAST_PORT = 5007         # Multicast port
BUF = 4096                # UDP buffer size
```

### Message Types

**Election Messages:**
- `HS_ELECTION` - Election message with hop count
- `HS_REPLY` - Reply to election message
- `HS_LEADER` - Leader announcement

**Client Messages:**
- `REGISTER` - Client registration
- `CREATE_GROUP` - Create new group
- `JOIN_GROUP` - Join existing group
- `START_VOTE` - Initiate vote
- `VOTE_ACK` - Vote submission
- `VOTE_RESULT` - Vote results

**Replication Messages:**
- `REPL_STATE` - Full state transfer
- `REPL_REGISTER` - Client registration replication
- `REPL_VOTE` - Vote replication

**Heartbeat Messages:**
- `HEARTBEAT` - Liveness check
- `HEARTBEAT_ACK` - Heartbeat acknowledgment

---

## 📁 Project Structure

```
VoteCast/
├── server.py           # Main server class and logic
├── client.py           # Client application
├── config.py           # Configuration constants
├── discovery.py        # Service discovery via multicast
├── election.py         # Hirschberg-Sinclair algorithm
├── ring.py             # Ring topology management
├── handlers.py         # Client request handlers
├── fo_multicast.py     # FIFO-ordered reliable multicast
├── replication.py      # State replication logic
├── heartbeat.py        # Heartbeat utilities
├── requirements.txt    # Python dependencies
└── README.md           # This file
```

### Key Files

| File | Purpose |
|------|---------|
| `server.py` | Main server with threading, networking, and message routing |
| `election.py` | Implements Hirschberg-Sinclair leader election algorithm |
| `discovery.py` | Multicast-based service discovery and failure detection |
| `fo_multicast.py` | FIFO-ordered reliable multicast with retransmissions |
| `handlers.py` | Processes client requests (register, vote, groups) |
| `replication.py` | State transfer between old and new leader |

---

## 🎨 Console Output

### Server Output

- 🔍 **Discovery**: Service discovery messages
- ✅ **Joins** (Yellow): New servers joining
- 💀 **Crashes** (Red): Server failures
- 🗳️ **Elections**: Election triggered
- **HS Messages** (Green): Leader election progress
- 💔 **Heartbeats**: Failure detection

### Client Output

- **Leader Discovery**: Finding the current leader
- **Registration**: Successful authentication
- **Votes**: New votes available
- **Results**: Vote outcomes

---

## 🐛 Troubleshooting

### Servers not discovering each other
- Verify all servers use the same `MCAST_GRP` and `MCAST_PORT`
- Check firewall settings for multicast traffic
- Ensure multicast is enabled on your network interface

### Client can't find leader
- Make sure at least one server is running
- Wait 5-10 seconds for election to complete
- Check server logs for leader election messages

### Address already in use
```bash
# Kill process on specific port
lsof -ti:6001 | xargs kill -9
```

---

## 🤝 Contributing

Contributions are welcome! Here are some ideas:

- [ ] Add persistent storage (database)
- [ ] Implement encrypted communication
- [ ] Add web UI for voting
- [ ] Support for weighted votes
- [ ] Anonymous voting mode
- [ ] Vote history and analytics
- [ ] Support for ranked-choice voting

---

## 📄 License

This project is open source and available under the MIT License.

---

## 🙏 Acknowledgments

This project demonstrates distributed systems concepts including:
- Leader election algorithms (Hirschberg-Sinclair)
- Fault tolerance and failure detection
- State replication
- FIFO-ordered reliable multicast
- Ring topologies

Built as a learning project to explore distributed systems design patterns.

---

## 📞 Contact

For questions or feedback, please open an issue on GitHub.

---

**Happy Voting! 🗳️**
