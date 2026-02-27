"""
Flask + WebSocket bridge to the TCP chat server.

Runs a web server on port 8080. Each browser tab that connects
gets its own TCP socket to the TCPServer on localhost:5050.
Messages flow:  Browser <-> WebSocket <-> Flask <-> TCP Socket <-> TCPServer
"""

import socket
import threading
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "tcp-webui-secret"
socketio = SocketIO(app, cors_allowed_origins="*")

# Track TCP connections per WebSocket session
tcp_connections = {}  # sid -> tcp_socket
tcp_lock = threading.Lock()

TCP_HOST = "localhost"
TCP_PORT = 5050


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    with tcp_lock:
        return {"connected_clients": len(tcp_connections)}


def tcp_receiver(sid, tcp_sock):
    """Background thread: read from TCP socket and push to the browser via WebSocket."""
    while True:
        try:
            tcp_sock.settimeout(1.0)
            data = tcp_sock.recv(4096)
            if not data:
                socketio.emit("chat_message", {
                    "type": "system",
                    "text": "Connection closed by server."
                }, to=sid)
                break
            text = data.decode().strip()
            if not text:
                continue
            # Determine message type
            if text.startswith("[Server]"):
                msg_type = "system"
            else:
                msg_type = "remote"
            socketio.emit("chat_message", {"type": msg_type, "text": text}, to=sid)
        except socket.timeout:
            continue
        except Exception:
            socketio.emit("chat_message", {
                "type": "system",
                "text": "Lost connection to server."
            }, to=sid)
            break

    # Cleanup
    try:
        tcp_sock.close()
    except Exception:
        pass
    with tcp_lock:
        tcp_connections.pop(sid, None)
    socketio.emit("tcp_status", {"connected": False}, to=sid)


@socketio.on("connect")
def handle_ws_connect():
    emit("tcp_status", {"connected": False})


@socketio.on("disconnect")
def handle_ws_disconnect():
    sid = getattr(handle_ws_disconnect, "_sid", None)
    from flask import request
    sid = request.sid
    with tcp_lock:
        tcp_sock = tcp_connections.pop(sid, None)
    if tcp_sock:
        try:
            tcp_sock.close()
        except Exception:
            pass


@socketio.on("tcp_connect")
def handle_tcp_connect(data=None):
    """Browser requests a new TCP connection to the chat server."""
    from flask import request
    sid = request.sid

    host = TCP_HOST
    port = TCP_PORT
    if data:
        host = data.get("host", host)
        port = int(data.get("port", port))

    # Close existing connection if any
    with tcp_lock:
        old = tcp_connections.pop(sid, None)
    if old:
        try:
            old.close()
        except Exception:
            pass

    try:
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.connect((host, port))
    except Exception as e:
        emit("tcp_status", {"connected": False, "error": str(e)})
        return

    with tcp_lock:
        tcp_connections[sid] = tcp_sock

    emit("tcp_status", {"connected": True, "host": host, "port": port})

    # Start background receiver thread
    t = threading.Thread(target=tcp_receiver, args=(sid, tcp_sock), daemon=True)
    t.start()


@socketio.on("tcp_disconnect")
def handle_tcp_disconnect():
    from flask import request
    sid = request.sid
    with tcp_lock:
        tcp_sock = tcp_connections.pop(sid, None)
    if tcp_sock:
        try:
            tcp_sock.close()
        except Exception:
            pass
    emit("tcp_status", {"connected": False})


@socketio.on("send_message")
def handle_send_message(data):
    """Browser sends a chat message -> forward to TCP server."""
    from flask import request
    sid = request.sid
    text = data.get("text", "").strip()
    if not text:
        return

    with tcp_lock:
        tcp_sock = tcp_connections.get(sid)

    if not tcp_sock:
        emit("chat_message", {"type": "system", "text": "Not connected to server."})
        return

    try:
        tcp_sock.send(text.encode())
        emit("chat_message", {"type": "self", "text": text})
    except Exception:
        emit("chat_message", {"type": "system", "text": "Failed to send message."})


if __name__ == "__main__":
    print("[WebUI] Starting on http://localhost:8080")
    print("[WebUI] Make sure TCPServer.py is running on port 5050")
    socketio.run(app, host="0.0.0.0", port=8080, debug=True, allow_unsafe_werkzeug=True)
