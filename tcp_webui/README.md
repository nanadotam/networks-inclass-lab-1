# TCP Chat — Walkthrough

## How It All Works

You have **3 components** that can be mixed and matched:

### 1. `TCPServer.py` — The Chat Server (the backbone)

This is a raw TCP socket server. It:

1. **Binds** to all network interfaces (`HOST = ''`) on **port 8080**
2. **Listens** for incoming TCP connections (`server_socket.listen(50)` — up to 50 queued)
3. For **each client** that connects, it spawns a new **thread** (`handle_client`) so multiple people can chat simultaneously
4. **Broadcasts** messages — when one client sends a message, the server forwards it to every *other* connected client
5. Assigns each client a username based on their IP/port (e.g. `User_10_255_163_145_54321`)

**Data flow:**

```
Client A sends "hello" → Server receives → Server forwards to Client B, C, D...
```

### 2. `TCPClient.py` — Terminal-Based Chat Client

A simple command-line client that:

1. Opens a TCP socket and **connects** to a server IP + port
2. Runs a **background thread** to continuously listen for incoming messages
3. The **main thread** reads your keyboard input and sends it to the server
4. Supports command-line args: `python TCPClient.py <host> <port>`

### 3. `app.py` — Web UI (Flask + WebSocket Bridge)

This is a **web frontend** that acts as a middleman:

```
Browser <--WebSocket--> Flask app <--TCP Socket--> TCPServer
```

It:

1. Serves a chat webpage on **port 8080** (HTTP)
2. Each browser tab that clicks "Connect" gets its own **TCP socket** to the TCP server
3. Uses **Socket.IO** (WebSockets) to relay messages between the browser and the TCP socket in real-time
4. The web UI lets you type a host/port in the browser and connect to any TCP server

---

## How to Start Things Up

### Option A: Terminal-Only Chat (no web UI)

**Step 1 — Start the server (you):**

```bash
cd tcp_webui
python TCPServer.py
```

You'll see: `[Server] Listening on port 8080...`

**Step 2 — Your friend connects:**

```bash
python TCPClient.py <your-ip> 8080
```

You can also type messages from the server terminal — they broadcast to all clients.

### Option B: Web UI Chat

There's a conflict — both `TCPServer.py` and `app.py` default to **port 8080**. You need to change one.

**Step 1 — Change `TCPServer.py` to use port 5050** (which is what `app.py` expects):

Edit line 6 of `TCPServer.py`:

```python
PORT = 5050  # instead of 8080
```

**Step 2 — Start the TCP server:**

```bash
python TCPServer.py
```

**Step 3 — Start the web UI:**

```bash
python app.py
```

**Step 4 — Open your browser** to `http://localhost:8080`. You'll see the chat UI. Type `localhost` and `5050` in the host/port fields and click **Connect**.

**Step 5 — Your friend** can either:

- Use the terminal client: `python TCPClient.py <your-ip> 5050`
- Or if they have the web UI running too, they enter your IP and port `5050` in their browser

---

## Quick Reference

| File | What it does | Default port | Command |
|------|-------------|-------------|---------|
| `TCPServer.py` | Chat server | 8080 | `python TCPServer.py` |
| `TCPClient.py` | Terminal client | connects to 5050 | `python TCPClient.py <host> <port>` |
| `app.py` | Web UI bridge | 8080 (web), connects to 5050 (TCP) | `python app.py` |
