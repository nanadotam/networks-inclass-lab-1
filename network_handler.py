# network_handler.py
import socket
import struct
import threading
import time
from collections import deque


class JitterBuffer:
    """
    Minimal jitter buffer:
    - store packets as (seq, timestamp_ms, audio_bytes)
    - keep sorted by sequence number
    - capacity ~ 2-3 packets (40-60ms)
    """
    def __init__(self, capacity: int = 3):
        self.capacity = max(1, capacity)
        self._buf = []
        self._lock = threading.Lock()
        self.last_played_seq = -1

    def add(self, seq: int, ts_ms: int, audio: bytes):
        with self._lock:
            # Drop duplicates (already played or already buffered)
            if seq <= self.last_played_seq:
                return
            for (s, _, _) in self._buf:
                if s == seq:
                    return

            self._buf.append((seq, ts_ms, audio))
            self._buf.sort(key=lambda x: x[0])

            # If over capacity, drop the oldest (lowest seq)
            while len(self._buf) > self.capacity:
                self._buf.pop(0)

    def pop_next(self):
        """
        Returns (seq, ts_ms, audio) if available; otherwise None
        """
        with self._lock:
            if not self._buf:
                return None
            pkt = self._buf.pop(0)
            self.last_played_seq = pkt[0]
            return pkt

    def occupancy(self) -> int:
        with self._lock:
            return len(self._buf)


class NetworkHandler:
    """
    UDP send/receive with:
    - Packet format: 8-byte header + PCM payload
      Header: sequence(uint32) + timestamp_ms(uint32) packed with network byte order "!II"
    - Receiver thread pushes into jitter buffer
    - get_playable_audio_frame() pops from jitter buffer or returns silence
    - Basic metrics: loss %, throughput, buffer occupancy, latency estimate
    """
    HEADER_FMT = "!II"
    HEADER_SIZE = 8

    def __init__(self, local_port: int, peer_ip: str, peer_port: int, jitter_capacity: int = 3):
        self.peer_addr = (peer_ip, peer_port)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", local_port))
        self.sock.settimeout(0.2)

        self.jb = JitterBuffer(capacity=jitter_capacity)

        # TX
        self._tx_seq = 0
        self._tx_bytes_window = deque()  # (timestamp, bytes_sent) for throughput calc

        # RX metrics
        self._rx_expected_seq = None
        self._rx_received = 0
        self._rx_expected = 0
        self._rx_last_seq = -1
        self._latency_ms = 0.0

        self._rx_lock = threading.Lock()
        self._running = threading.Event()
        self._running.set()

        self._receiver_thread = None

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _pack(self, audio_data: bytes) -> bytes:
        ts = self._now_ms()
        header = struct.pack(self.HEADER_FMT, self._tx_seq, ts)
        pkt = header + audio_data
        self._tx_seq = (self._tx_seq + 1) & 0xFFFFFFFF
        return pkt

    def _unpack(self, packet: bytes):
        if len(packet) < self.HEADER_SIZE:
            return None
        header = packet[: self.HEADER_SIZE]
        audio = packet[self.HEADER_SIZE :]
        seq, ts = struct.unpack(self.HEADER_FMT, header)
        return seq, ts, audio

    def send_audio_frame(self, audio_data: bytes):
        pkt = self._pack(audio_data)
        sent = self.sock.sendto(pkt, self.peer_addr)

        # throughput tracking (rolling 1 second window)
        now = time.time()
        self._tx_bytes_window.append((now, sent))
        while self._tx_bytes_window and (now - self._tx_bytes_window[0][0] > 1.0):
            self._tx_bytes_window.popleft()

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

            parsed = self._unpack(data)
            if not parsed:
                continue

            seq, ts_ms, audio = parsed

            # latency estimate
            now_ms = self._now_ms()
            latency = max(0, now_ms - ts_ms)

            # update RX metrics + loss estimation
            with self._rx_lock:
                self._rx_received += 1
                self._rx_last_seq = seq
                self._latency_ms = latency

                if self._rx_expected_seq is None:
                    # First packet seen => initialize expected baseline
                    self._rx_expected_seq = seq
                    self._rx_expected = 1
                else:
                    # expected increases by how far sequence advanced (incl out-of-order)
                    if seq >= self._rx_expected_seq:
                        gap = (seq - self._rx_expected_seq) + 1
                        self._rx_expected += gap
                        self._rx_expected_seq = seq + 1
                    else:
                        # out-of-order packet; expected count unchanged
                        pass

            # push into jitter buffer (handles reorder/duplicate)
            self.jb.add(seq, ts_ms, audio)

    def get_playable_audio_frame(self) -> bytes:
        pkt = self.jb.pop_next()
        if pkt is None:
            # caller (audio handler) can treat empty as silence
            return b""
        _seq, _ts, audio = pkt
        return audio

    def get_metrics_snapshot(self) -> dict:
        with self._rx_lock:
            expected = self._rx_expected
            received = self._rx_received
            loss = 0.0
            if expected > 0:
                loss = max(0.0, (expected - received) / expected * 100.0)

            # throughput in KB/s (payload+header actually sent)
            now = time.time()
            total_bytes = sum(b for (t, b) in self._tx_bytes_window if now - t <= 1.0)
            kbps = total_bytes / 1024.0

            return {
                "tx_seq": self._tx_seq,
                "rx_last_seq": self._rx_last_seq,
                "loss_percent": loss,
                "throughput_kbps": kbps,
                "buffer_occupancy": self.jb.occupancy(),
                "buffer_capacity": self.jb.capacity,
                "latency_ms": float(self._latency_ms),
            }

    def close(self):
        self._running.clear()
        try:
            self.sock.close()
        except Exception:
            pass