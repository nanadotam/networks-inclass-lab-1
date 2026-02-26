# network_handler.py
import socket
import threading
import time

from jitter_buffer import JitterBuffer, create_packet, parse_packet, CHUNK, SILENCE
from metrics import NetworkMetrics


class NetworkHandler:
    """
    UDP send/receive handler that integrates with jitter_buffer.py and metrics.py.

    - Packet format: 8-byte header + PCM payload (320 bytes)
      Header: sequence(uint32) + timestamp_ms(uint32) packed with "!II"
    - Receiver thread pushes into JitterBuffer
    - get_playable_audio_frame() pops from JitterBuffer or returns silence
    - Metrics tracked via NetworkMetrics
    """

    def __init__(self, local_port: int, peer_ip: str, peer_port: int, jitter_capacity: int = 3):
        self.peer_addr = (peer_ip, peer_port)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", local_port))
        self.sock.settimeout(0.2)

        self.jb = JitterBuffer(capacity=jitter_capacity)
        self.metrics = NetworkMetrics()
        self.metrics.set_addresses(("0.0.0.0", local_port), (peer_ip, peer_port))

        # TX
        self._tx_seq = 0

        self._running = threading.Event()
        self._running.set()

        self._receiver_thread = None

    def send_audio_frame(self, audio_data: bytes):
        packet = create_packet(audio_data, self._tx_seq)
        self.sock.sendto(packet, self.peer_addr)
        self._tx_seq = (self._tx_seq + 1) & 0xFFFFFFFF
        self.metrics.record_sent(len(audio_data))

    def start_receiver(self):
        if self._receiver_thread and self._receiver_thread.is_alive():
            return
        self._receiver_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._receiver_thread.start()

    def _recv_loop(self):
        while self._running.is_set():
            try:
                data, _addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            if len(data) < 8:
                continue

            seq, ts_ms, audio = parse_packet(data)
            self.jb.add((seq, ts_ms, audio))
            self.metrics.record_received(seq, ts_ms, len(audio))

    def get_playable_audio_frame(self) -> bytes:
        audio = self.jb.get_next()
        if audio is None:
            return b""
        return audio

    def get_metrics_snapshot(self) -> dict:
        summary = self.metrics.get_summary()
        buf_occ, buf_cap = self.jb.get_occupancy()
        return {
            "tx_seq": self._tx_seq,
            "rx_last_seq": summary['recv_seq'],
            "loss_percent": summary['packet_loss'],
            "throughput_kbps": summary['recv_throughput'] / 1024.0,
            "buffer_occupancy": buf_occ,
            "buffer_capacity": buf_cap,
            "latency_ms": summary['latency_ms'],
        }

    def close(self):
        self._running.clear()
        try:
            self.sock.close()
        except Exception:
            pass
