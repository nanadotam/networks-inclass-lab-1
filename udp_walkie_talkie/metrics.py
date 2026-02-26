import time
import threading


class NetworkMetrics:
    """
    Tracks real-time network statistics for the UDP walkie-talkie.

    Metrics tracked:
    - Sequence numbers (sent and received)
    - Packet loss percentage
    - Throughput in bytes/second
    - Latency estimate from timestamp deltas
    - Buffer occupancy
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        """Reset all metrics to initial state."""
        with self.lock:
            # Sequence tracking
            self.send_seq = 0
            self.recv_count = 0
            self.expected_seq = -1       # Highest sequence seen + 1
            self.highest_recv_seq = -1

            # Throughput tracking
            self.bytes_sent = 0
            self.bytes_received = 0
            self._throughput_start = time.time()
            self._last_throughput_sent = 0.0
            self._last_throughput_recv = 0.0

            # Latency
            self.last_latency_ms = 0.0

            # Source/dest info
            self.local_addr = ("0.0.0.0", 0)
            self.peer_addr = ("0.0.0.0", 0)

    def record_sent(self, num_bytes):
        """Record a sent packet."""
        with self.lock:
            self.send_seq += 1
            self.bytes_sent += num_bytes

    def record_received(self, seq, timestamp, num_bytes):
        """Record a received packet with its sequence number and timestamp."""
        with self.lock:
            self.recv_count += 1
            self.bytes_received += num_bytes

            # Track highest sequence seen to estimate expected packets
            if seq > self.highest_recv_seq:
                self.highest_recv_seq = seq

            # Estimate latency from packet timestamp
            now_ms = int(time.time() * 1000)
            # Handle uint32 wrap-around
            delta = (now_ms - timestamp) % (2**32)
            # Only use reasonable latency values (< 5 seconds)
            if delta < 5000:
                self.last_latency_ms = delta

    def get_packet_loss(self):
        """
        Calculate packet loss percentage.
        Loss = (Expected - Received) / Expected × 100
        """
        with self.lock:
            if self.highest_recv_seq < 0:
                return 0.0
            expected = self.highest_recv_seq + 1  # Sequences are 0-based
            if expected == 0:
                return 0.0
            lost = expected - self.recv_count
            return max(0.0, (lost / expected) * 100.0)

    def get_throughput(self):
        """
        Calculate throughput in bytes/second for both sent and received.
        Returns (send_bps, recv_bps).
        """
        with self.lock:
            now = time.time()
            elapsed = now - self._throughput_start
            if elapsed < 0.1:
                return self._last_throughput_sent, self._last_throughput_recv

            send_bps = self.bytes_sent / elapsed
            recv_bps = self.bytes_received / elapsed

            self._last_throughput_sent = send_bps
            self._last_throughput_recv = recv_bps
            return send_bps, recv_bps

    def get_latency(self):
        """Return the last estimated one-way latency in milliseconds."""
        with self.lock:
            return self.last_latency_ms

    def set_addresses(self, local_addr, peer_addr):
        """Set the local and peer addresses for display."""
        with self.lock:
            self.local_addr = local_addr
            self.peer_addr = peer_addr

    def get_summary(self):
        """
        Return a dict with all current metrics for the UI debug panel.
        """
        send_bps, recv_bps = self.get_throughput()
        loss = self.get_packet_loss()
        latency = self.get_latency()

        with self.lock:
            return {
                'send_seq': self.send_seq,
                'recv_seq': self.highest_recv_seq,
                'recv_count': self.recv_count,
                'packet_loss': loss,
                'send_throughput': send_bps,
                'recv_throughput': recv_bps,
                'latency_ms': latency,
                'local_addr': self.local_addr,
                'peer_addr': self.peer_addr,
            }

    def format_debug_line(self, buffer_occupancy=0, buffer_capacity=3):
        """
        Format a single-line debug string for the overlay.
        Example: SEQ: 00001234 | LOSS: 2.3% THRU: 8.1 KB/s | LAT: 35ms BUFFER: 2/3
        """
        summary = self.get_summary()
        seq = summary['recv_seq'] if summary['recv_seq'] >= 0 else summary['send_seq']
        thru = summary['recv_throughput'] / 1024.0  # Convert to KB/s
        return (
            f"SEQ: {seq:08d} | "
            f"LOSS: {summary['packet_loss']:.1f}% "
            f"THRU: {thru:.1f} KB/s | "
            f"LAT: {summary['latency_ms']:.0f}ms "
            f"BUFFER: {buffer_occupancy}/{buffer_capacity}"
        )
