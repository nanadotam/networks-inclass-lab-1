import socket
import threading

# Server configuration
HOST = ''
PORT = 5050

# Track all connected clients: {conn: username}
clients = {}
clients_lock = threading.Lock()


def generate_username(address):
    """Generate a pseudonym from client IP address and port."""
    ip = address[0].replace('.', '_')
    port = address[1]
    return f"User_{ip}_{port}"


def broadcast(message, sender_conn=None):
    """Send a message to all connected clients except the sender."""
    with clients_lock:
        for conn in list(clients):
            if conn != sender_conn:
                try:
                    conn.send(message.encode())
                except:
                    conn.close()
                    del clients[conn]


def handle_client(conn, address):
    """Handle a single client connection in its own thread."""
    username = generate_username(address)

    with clients_lock:
        clients[conn] = username

    print(f"[+] {username} connected from {address[0]}:{address[1]}")
    print(f"[*] Active connections: {len(clients)}")

    # Notify everyone that a new user joined
    broadcast(f"[Server] {username} has joined the chat.\n", sender_conn=conn)
    conn.send(f"[Server] Welcome, {username}! You can start chatting.\n".encode())

    try:
        while True:
            data = conn.recv(1024)
            if not data:
                break  # Client disconnected

            message = data.decode().strip()
            if not message:
                continue

            print(f"[{username}]: {message}")

            # Broadcast to everyone else
            broadcast(f"[{username}]: {message}\n", sender_conn=conn)

    except ConnectionResetError:
        pass
    finally:
        with clients_lock:
            if conn in clients:
                del clients[conn]
        conn.close()
        print(f"[-] {username} disconnected. Active connections: {len(clients)}")
        broadcast(f"[Server] {username} has left the chat.\n")


def server_input():
    """Allow the server to type and broadcast messages to all clients."""
    print("[Server] You can type messages below to broadcast to all clients.\n")
    while True:
        try:
            print("Server: ", end='', flush=True)
            message = input()
            if not message.strip():
                continue
            broadcast(f"[Server]: {message}\n")
        except (KeyboardInterrupt, EOFError):
            break


def start_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(50)

    print(f"[Server] Listening on port {PORT}...")
    print("[Server] Waiting for connections. Press Ctrl+C to stop.\n")

    # Only start input thread if stdin is a terminal (not backgrounded)
    import sys
    if sys.stdin.isatty():
        input_thread = threading.Thread(target=server_input, daemon=True)
        input_thread.start()

    try:
        while True:
            conn, address = server_socket.accept()
            thread = threading.Thread(target=handle_client, args=(conn, address), daemon=True)
            thread.start()
    except KeyboardInterrupt:
        print("\n[Server] Shutting down...")
    finally:
        server_socket.close()


if __name__ == "__main__":
    start_server()