import tkinter as tk
from tkinter import font as tkfont
import threading
import time
from socket import socket, AF_INET, SOCK_DGRAM

from jitter_buffer import JitterBuffer, parse_packet, create_packet, SILENCE, SAMPLES_PER_FRAME
from metrics import NetworkMetrics

# Audio constants from PRD
RATE = 8000
CHANNELS = 1
FRAME_DURATION_MS = 20

# Try to import pyaudio - may not be installed yet
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False


class WalkieTalkieApp:
    """
    Tkinter-based UI for the UDP Walkie-Talkie application.

    Features:
    - Connection settings (Peer IP, Port, Connect button)
    - Push-to-Talk button (click or hold SPACE)
    - Status LED indicator (white=idle, red=transmitting, green=receiving)
    - Live debug panel with network metrics
    """

    def __init__(self, root):
        self.root = root
        self.root.title("UDP WALKIE-TALKIE")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        # State
        self.is_connected = False
        self.is_transmitting = False
        self.is_receiving = False
        self.running = False

        # Network components
        self.sock = None
        self.peer_ip = ""
        self.peer_port = 5000
        self.local_port = 5000
        self.sequence_number = 0

        # Core modules
        self.jitter_buffer = JitterBuffer(capacity=3)
        self.metrics = NetworkMetrics()

        # Audio
        self.audio = None
        self.input_stream = None
        self.output_stream = None

        # Threads
        self.recv_thread = None
        self.playback_thread = None

        # Build UI
        self._build_ui()

        # Bind keyboard events
        self.root.bind("<KeyPress-space>", self._on_space_press)
        self.root.bind("<KeyRelease-space>", self._on_space_release)

        # Start metrics update loop
        self._update_metrics_display()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        """Build the complete Tkinter interface."""
        # Colors
        bg = "#1a1a2e"
        fg = "#eaeaea"
        accent = "#e94560"
        panel_bg = "#16213e"
        entry_bg = "#0f3460"

        # Fonts
        title_font = tkfont.Font(family="Helvetica", size=16, weight="bold")
        label_font = tkfont.Font(family="Helvetica", size=11)
        mono_font = tkfont.Font(family="Courier", size=10)
        btn_font = tkfont.Font(family="Helvetica", size=12, weight="bold")
        ptt_font = tkfont.Font(family="Helvetica", size=14, weight="bold")

        # ── Title ──
        title_frame = tk.Frame(self.root, bg=accent, pady=8)
        title_frame.pack(fill=tk.X)
        tk.Label(
            title_frame, text="UDP WALKIE-TALKIE",
            font=title_font, bg=accent, fg="white"
        ).pack()

        # ── Connection Settings ──
        conn_frame = tk.LabelFrame(
            self.root, text=" Connection Settings ",
            font=label_font, bg=panel_bg, fg=fg,
            padx=10, pady=8
        )
        conn_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        # Peer IP
        ip_row = tk.Frame(conn_frame, bg=panel_bg)
        ip_row.pack(fill=tk.X, pady=2)
        tk.Label(
            ip_row, text="Peer IP:", font=label_font,
            bg=panel_bg, fg=fg, width=8, anchor="w"
        ).pack(side=tk.LEFT)
        self.ip_entry = tk.Entry(
            ip_row, font=label_font, bg=entry_bg, fg=fg,
            insertbackground=fg, relief=tk.FLAT, width=20
        )
        self.ip_entry.insert(0, "localhost")
        self.ip_entry.pack(side=tk.LEFT, padx=(5, 0), fill=tk.X, expand=True)

        # Port
        port_row = tk.Frame(conn_frame, bg=panel_bg)
        port_row.pack(fill=tk.X, pady=2)
        tk.Label(
            port_row, text="Port:", font=label_font,
            bg=panel_bg, fg=fg, width=8, anchor="w"
        ).pack(side=tk.LEFT)
        self.port_entry = tk.Entry(
            port_row, font=label_font, bg=entry_bg, fg=fg,
            insertbackground=fg, relief=tk.FLAT, width=8
        )
        self.port_entry.insert(0, "5000")
        self.port_entry.pack(side=tk.LEFT, padx=(5, 0))

        # Local port (what we bind to)
        tk.Label(
            port_row, text="Local:", font=label_font,
            bg=panel_bg, fg=fg, width=6, anchor="e"
        ).pack(side=tk.LEFT, padx=(10, 0))
        self.local_port_entry = tk.Entry(
            port_row, font=label_font, bg=entry_bg, fg=fg,
            insertbackground=fg, relief=tk.FLAT, width=8
        )
        self.local_port_entry.insert(0, "5001")
        self.local_port_entry.pack(side=tk.LEFT, padx=(5, 0))

        # Connect button
        self.connect_btn = tk.Button(
            conn_frame, text="Connect", font=btn_font,
            bg=accent, fg="white", activebackground="#c0392b",
            activeforeground="white", relief=tk.FLAT,
            cursor="hand2", command=self._toggle_connection
        )
        self.connect_btn.pack(fill=tk.X, pady=(8, 2))

        # ── Push-to-Talk Area ──
        ptt_frame = tk.Frame(self.root, bg=bg, pady=10)
        ptt_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        # Status LED
        self.status_canvas = tk.Canvas(
            ptt_frame, width=30, height=30,
            bg=bg, highlightthickness=0
        )
        self.status_canvas.pack(pady=(5, 0))
        self.led = self.status_canvas.create_oval(
            5, 5, 25, 25, fill="#888888", outline="#555555", width=2
        )

        # Status label
        self.status_label = tk.Label(
            ptt_frame, text="Status: Disconnected",
            font=label_font, bg=bg, fg="#888888"
        )
        self.status_label.pack(pady=(2, 8))

        # PTT Button
        self.ptt_btn = tk.Button(
            ptt_frame, text="PUSH TO TALK\n(Hold SPACE or Click)",
            font=ptt_font, bg="#2d3436", fg="#888888",
            activebackground="#e94560", activeforeground="white",
            relief=tk.RAISED, bd=3, height=3,
            state=tk.DISABLED
        )
        self.ptt_btn.pack(fill=tk.X, padx=20, pady=5)
        self.ptt_btn.bind("<ButtonPress-1>", self._on_ptt_press)
        self.ptt_btn.bind("<ButtonRelease-1>", self._on_ptt_release)

        # ── Debug Panel ──
        debug_frame = tk.LabelFrame(
            self.root, text=" Network Debug ",
            font=label_font, bg=panel_bg, fg=fg,
            padx=10, pady=8
        )
        debug_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        self.debug_labels = {}
        debug_items = [
            ("seq", "Seq #:"),
            ("loss", "Packet Loss:"),
            ("throughput", "Throughput:"),
            ("buffer", "Buffer:"),
            ("latency", "Latency:"),
            ("addresses", "Route:"),
        ]

        for key, label_text in debug_items:
            row = tk.Frame(debug_frame, bg=panel_bg)
            row.pack(fill=tk.X, pady=1)
            tk.Label(
                row, text=label_text, font=mono_font,
                bg=panel_bg, fg="#e94560", width=14, anchor="w"
            ).pack(side=tk.LEFT)
            val_label = tk.Label(
                row, text="--", font=mono_font,
                bg=panel_bg, fg=fg, anchor="w"
            )
            val_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.debug_labels[key] = val_label

        # Debug one-liner (monospace overlay style from PRD)
        self.debug_oneliner = tk.Label(
            debug_frame, text="", font=mono_font,
            bg="#0a0a1a", fg="#00ff88", anchor="w",
            padx=5, pady=3
        )
        self.debug_oneliner.pack(fill=tk.X, pady=(5, 0))

    # ── Connection Management ──

    def _toggle_connection(self):
        if self.is_connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        """Initialize UDP socket and start receive thread."""
        self.peer_ip = self.ip_entry.get().strip()
        try:
            self.peer_port = int(self.port_entry.get().strip())
            self.local_port = int(self.local_port_entry.get().strip())
        except ValueError:
            self.status_label.config(text="Status: Invalid port", fg="#e94560")
            return

        try:
            # Create UDP socket (based on UDPClient/UDPServer pattern)
            self.sock = socket(AF_INET, SOCK_DGRAM)
            self.sock.bind(('', self.local_port))
            self.sock.settimeout(0.5)  # Non-blocking with timeout

            # Update metrics with address info
            self.metrics.reset()
            self.metrics.set_addresses(
                ("0.0.0.0", self.local_port),
                (self.peer_ip, self.peer_port)
            )

            # Init audio if available
            if PYAUDIO_AVAILABLE:
                self.audio = pyaudio.PyAudio()
                self.input_stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=SAMPLES_PER_FRAME
                )
                self.output_stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=CHANNELS,
                    rate=RATE,
                    output=True,
                    frames_per_buffer=SAMPLES_PER_FRAME
                )

            self.is_connected = True
            self.running = True
            self.sequence_number = 0
            self.jitter_buffer.clear()

            # Start receive thread
            self.recv_thread = threading.Thread(
                target=self._receive_loop, daemon=True
            )
            self.recv_thread.start()

            # Start playback thread
            self.playback_thread = threading.Thread(
                target=self._playback_loop, daemon=True
            )
            self.playback_thread.start()

            # Update UI state
            self.connect_btn.config(text="Disconnect", bg="#27ae60")
            self.ptt_btn.config(state=tk.NORMAL, fg="#eaeaea")
            self.ip_entry.config(state=tk.DISABLED)
            self.port_entry.config(state=tk.DISABLED)
            self.local_port_entry.config(state=tk.DISABLED)
            self._set_status("idle")

        except OSError as e:
            self.status_label.config(
                text=f"Status: Error - {e}", fg="#e94560"
            )

    def _disconnect(self):
        """Close socket and stop threads."""
        self.running = False
        self.is_connected = False
        self.is_transmitting = False

        # Close audio streams
        if self.input_stream:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception:
                pass
            self.input_stream = None

        if self.output_stream:
            try:
                self.output_stream.stop_stream()
                self.output_stream.close()
            except Exception:
                pass
            self.output_stream = None

        if self.audio:
            try:
                self.audio.terminate()
            except Exception:
                pass
            self.audio = None

        # Close socket
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

        self.jitter_buffer.clear()

        # Update UI
        self.connect_btn.config(text="Connect", bg="#e94560")
        self.ptt_btn.config(state=tk.DISABLED, fg="#888888")
        self.ip_entry.config(state=tk.NORMAL)
        self.port_entry.config(state=tk.NORMAL)
        self.local_port_entry.config(state=tk.NORMAL)
        self._set_status("disconnected")

    # ── Audio Transmission ──

    def _start_transmitting(self):
        """Start capturing and sending audio."""
        if not self.is_connected or self.is_transmitting:
            return
        self.is_transmitting = True
        self._set_status("transmitting")

        # Start capture thread
        capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True
        )
        capture_thread.start()

    def _stop_transmitting(self):
        """Stop capturing audio."""
        self.is_transmitting = False
        if self.is_connected:
            self._set_status("idle")

    def _capture_loop(self):
        """Continuously capture mic audio and send via UDP while transmitting."""
        while self.is_transmitting and self.running:
            try:
                if self.input_stream and PYAUDIO_AVAILABLE:
                    audio_data = self.input_stream.read(
                        SAMPLES_PER_FRAME, exception_on_overflow=False
                    )
                else:
                    # No PyAudio: send silence (for testing UI without audio)
                    audio_data = SILENCE
                    time.sleep(FRAME_DURATION_MS / 1000.0)

                # Create packet with header and send
                packet = create_packet(audio_data, self.sequence_number)
                self.sock.sendto(packet, (self.peer_ip, self.peer_port))

                self.sequence_number += 1
                self.metrics.record_sent(len(audio_data))

            except OSError:
                break
            except Exception:
                break

    def _receive_loop(self):
        """Listen for incoming UDP packets and add to jitter buffer."""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                if len(data) < 8:
                    continue

                seq, timestamp, audio_data = parse_packet(data)
                self.jitter_buffer.add((seq, timestamp, audio_data))
                self.metrics.record_received(seq, timestamp, len(audio_data))

                # Update receiving indicator
                if not self.is_transmitting:
                    self.is_receiving = True

            except TimeoutError:
                self.is_receiving = False
                continue
            except OSError:
                break

    def _playback_loop(self):
        """Continuously pull from jitter buffer and play audio."""
        while self.running:
            audio_data = self.jitter_buffer.get_next()
            if audio_data is not None:
                if self.output_stream and PYAUDIO_AVAILABLE:
                    try:
                        self.output_stream.write(audio_data)
                    except Exception:
                        pass
                if not self.is_transmitting:
                    self.is_receiving = True
            else:
                self.is_receiving = False
                time.sleep(FRAME_DURATION_MS / 1000.0)

    # ── PTT Event Handlers ──

    def _on_ptt_press(self, event=None):
        self._start_transmitting()

    def _on_ptt_release(self, event=None):
        self._stop_transmitting()

    def _on_space_press(self, event=None):
        if self.is_connected and not self.is_transmitting:
            self._start_transmitting()

    def _on_space_release(self, event=None):
        self._stop_transmitting()

    # ── Status Display ──

    def _set_status(self, state):
        """Update LED and status label based on state."""
        colors = {
            "disconnected": ("#888888", "Disconnected"),
            "idle":         ("#ffffff", "Idle - Ready"),
            "transmitting": ("#e74c3c", "Transmitting"),
            "receiving":    ("#2ecc71", "Receiving"),
        }
        color, text = colors.get(state, ("#888888", "Unknown"))
        self.status_canvas.itemconfig(self.led, fill=color)
        self.status_label.config(text=f"Status: {text}", fg=color)

    # ── Metrics Display Update ──

    def _update_metrics_display(self):
        """Update the debug panel every 100ms."""
        if self.is_connected:
            summary = self.metrics.get_summary()
            buf_occ, buf_cap = self.jitter_buffer.get_occupancy()

            # Update individual labels
            seq_display = max(summary['send_seq'], summary['recv_seq'])
            self.debug_labels["seq"].config(
                text=f"{seq_display:08d}"
            )
            self.debug_labels["loss"].config(
                text=f"{summary['packet_loss']:.1f}%"
            )
            thru_kb = summary['recv_throughput'] / 1024.0
            self.debug_labels["throughput"].config(
                text=f"{thru_kb:.1f} KB/s"
            )
            self.debug_labels["buffer"].config(
                text=f"{buf_occ}/{buf_cap} packets"
            )
            self.debug_labels["latency"].config(
                text=f"{summary['latency_ms']:.0f} ms"
            )
            local = summary['local_addr']
            peer = summary['peer_addr']
            self.debug_labels["addresses"].config(
                text=f"{local[0]}:{local[1]} -> {peer[0]}:{peer[1]}"
            )

            # Update one-liner overlay
            oneliner = self.metrics.format_debug_line(buf_occ, buf_cap)
            self.debug_oneliner.config(text=oneliner)

            # Update receiving status LED
            if self.is_receiving and not self.is_transmitting:
                self._set_status("receiving")
            elif self.is_transmitting:
                self._set_status("transmitting")
            else:
                self._set_status("idle")

        # Schedule next update (100ms as per PRD)
        self.root.after(100, self._update_metrics_display)

    def _on_close(self):
        """Clean shutdown."""
        self._disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = WalkieTalkieApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
