import socket
import threading
import sys

# Server connection settings
SERVER_NAME = 'localhost'
SERVER_PORT = 5050

stop_event = threading.Event()


def receive_messages(client_socket):
    """Continuously listen for incoming messages from the server."""
    while not stop_event.is_set():
        try:
            client_socket.settimeout(1.0)
            data = client_socket.recv(1024)
            if not data:
                print("\n[Server] Connection closed by server.")
                stop_event.set()
                break
            print(f"\n{data.decode().strip()}\nYou: ", end='', flush=True)
        except socket.timeout:
            continue
        except:
            if not stop_event.is_set():
                print("\n[Error] Lost connection to server.")
                stop_event.set()
            break

    client_socket.close()


def start_client():
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        client_socket.connect((SERVER_NAME, SERVER_PORT))
        print(f"[+] Connected to server at {SERVER_NAME}:{SERVER_PORT}")
        print("[*] Type your messages below. Type /quit to exit.\n")
    except ConnectionRefusedError:
        print(f"[Error] Could not connect to {SERVER_NAME}:{SERVER_PORT}. Is the server running?")
        sys.exit(1)

    # Start background thread for receiving
    receive_thread = threading.Thread(target=receive_messages, args=(client_socket,), daemon=True)
    receive_thread.start()

    # Main thread handles sending
    try:
        while not stop_event.is_set():
            print("You: ", end='', flush=True)
            try:
                message = input()
            except EOFError:
                break

            if stop_event.is_set():
                break

            if not message.strip():
                continue

            if message.lower() in ('/quit', '/exit'):
                print("[*] Disconnecting...")
                stop_event.set()
                break

            try:
                client_socket.send(message.encode())
            except:
                print("[Error] Failed to send message.")
                stop_event.set()
                break

    except KeyboardInterrupt:
        print("\n[*] Disconnecting...")
        stop_event.set()
    finally:
        client_socket.close()


if __name__ == "__main__":
    if len(sys.argv) == 3:
        SERVER_NAME = sys.argv[1]
        SERVER_PORT = int(sys.argv[2])
    elif len(sys.argv) == 2:
        SERVER_NAME = sys.argv[1]

    start_client()