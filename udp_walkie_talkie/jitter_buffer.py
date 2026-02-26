import threading
import struct
import time


# Audio constants matching PRD spec
# 20ms @ 8kHz = 160 samples, each 2 bytes (16-bit PCM) = 320 bytes per frame
SAMPLES_PER_FRAME = 160
BYTES_PER_SAMPLE = 2
CHUNK = SAMPLES_PER_FRAME * BYTES_PER_SAMPLE  # 320 bytes per frame
SILENCE = b'\x00' * CHUNK  # Silence frame for lost packets


class JitterBuffer:
    """
    Minimal jitter buffer for reordering out-of-order UDP audio packets.

    Holds 2-3 packets (40-60ms at 20ms/frame) and reorders them by
    sequence number before playback. Drops duplicates and late packets,
    synthesizes silence for gaps.
    """

    def __init__(self, capacity=3):
        self.buffer = []                # List of (seq, timestamp, audio_data)
        self.capacity = capacity        # Max packets to hold (default 3 = 60ms)
        self.last_played_seq = -1       # Last sequence number played out
        self.lock = threading.Lock()
        self.total_added = 0
        self.total_dropped = 0
        self.total_late = 0
        self.total_duplicate = 0

    def add(self, packet):
        """
        Add a packet (seq, timestamp, audio_data) to the buffer.
        Maintains sorted order by sequence number.
        Drops duplicates and packets that arrive too late.
        """
        seq, timestamp, audio_data = packet

        with self.lock:
            # Drop packets with sequence <= last played (too late)
            if seq <= self.last_played_seq:
                self.total_late += 1
                return False

            # Drop duplicate sequence numbers
            for existing in self.buffer:
                if existing[0] == seq:
                    self.total_duplicate += 1
                    return False

            # Insert maintaining sorted order by sequence
            self.buffer.append(packet)
            self.buffer.sort(key=lambda x: x[0])

            # If buffer exceeds capacity, drop the oldest (lowest seq)
            if len(self.buffer) > self.capacity:
                self.buffer.pop(0)
                self.total_dropped += 1

            self.total_added += 1
            return True

    def get_next(self):
        """
        Get the next audio frame for playback.
        Returns audio data bytes, or silence if buffer is empty.
        Handles gaps by synthesizing silence for missing sequence numbers.
        """
        with self.lock:
            if not self.buffer:
                return None  # No data available

            packet = self.buffer[0]
            seq = packet[0]

            # Check for gap: if we expect seq = last_played + 1 but got higher,
            # synthesize silence for the missing packet
            expected_seq = self.last_played_seq + 1
            if expected_seq >= 0 and seq > expected_seq:
                # Gap detected - return silence and advance expected
                self.last_played_seq = expected_seq
                return SILENCE

            # Normal case: pop and return the next packet
            self.buffer.pop(0)
            self.last_played_seq = seq
            return packet[2]  # Return audio data

    def get_occupancy(self):
        """Return current buffer occupancy as (current, max)."""
        with self.lock:
            return len(self.buffer), self.capacity

    def get_stats(self):
        """Return buffer statistics."""
        with self.lock:
            return {
                'added': self.total_added,
                'dropped': self.total_dropped,
                'late': self.total_late,
                'duplicate': self.total_duplicate,
                'occupancy': len(self.buffer),
                'capacity': self.capacity,
            }

    def clear(self):
        """Clear the buffer and reset state."""
        with self.lock:
            self.buffer.clear()
            self.last_played_seq = -1


def parse_packet(packet):
    """
    Parse a raw UDP datagram into (sequence, timestamp, audio_data).
    Packet format: 8-byte header (!II) + 160-byte audio payload.
    """
    header = packet[:8]
    audio_data = packet[8:]
    sequence, timestamp = struct.unpack("!II", header)
    return sequence, timestamp, audio_data


def create_packet(audio_data, sequence_number):
    """
    Create a UDP datagram from audio data with header.
    Returns bytes: 8-byte header + audio payload.
    """
    timestamp = int(time.time() * 1000) % (2**32)  # ms, wrap at uint32 max
    header = struct.pack("!II", sequence_number, timestamp)
    return header + audio_data
