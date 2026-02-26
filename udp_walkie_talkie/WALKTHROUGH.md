# UDP Walkie-Talkie - Technical Walkthrough

> A real-time push-to-talk voice communication system built with Python, UDP sockets, and Tkinter.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture at a Glance](#2-architecture-at-a-glance)
3. [File Breakdown](#3-file-breakdown)
   - [jitter_buffer.py](#31-jitter_bufferpy---packet-reordering-engine)
   - [metrics.py](#32-metricspy---network-statistics-tracker)
   - [ui.py](#33-uipy---the-full-interface)
4. [How the Packet Protocol Works](#4-how-the-packet-protocol-works)
5. [Data Flow: From Microphone to Speaker](#5-data-flow-from-microphone-to-speaker)
6. [Threading Model](#6-threading-model)
7. [How to Connect Two Devices](#7-how-to-connect-two-devices)
8. [Setup & Installation](#8-setup--installation)
9. [Troubleshooting](#9-troubleshooting)
10. [Integration with Team Files](#10-integration-with-team-files)

---

## 1. Project Overview

This is a **UDP-based walkie-talkie** app. Unlike TCP (which guarantees delivery), UDP is *fire-and-forget* - packets may arrive out of order, get duplicated, or be lost entirely. That's actually fine for voice: a slightly garbled syllable is better than waiting 200ms for a retransmission.

The app captures audio from your microphone, chops it into 20ms frames, wraps each frame in a small header (sequence number + timestamp), and fires it over UDP to a peer. The peer's jitter buffer reorders any out-of-order packets, fills in silence for lost ones, and feeds them to the speaker.

**Our 3 files handle:**

| File | Responsibility |
|------|---------------|
| `jitter_buffer.py` | Receives packets, reorders them, handles loss |
| `metrics.py` | Tracks packet loss, throughput, latency in real-time |
| `ui.py` | Tkinter GUI with PTT button, LED status, debug panel |

The rest of the team builds: `main.py` (entry point), `audio_handler.py` (PyAudio), `network_handler.py` (socket management).

---

## 2. Architecture at a Glance

```
┌─────────────────────────────────────────────────────────┐
│                     DEVICE A                            │
│                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────┐   │
│  │   UI     │───>│ Capture  │───>│  UDP Socket      │───┼──── Network ────>
│  │ (Tkinter)│    │ (PyAudio)│    │  sendto()        │   │
│  │          │    └──────────┘    └──────────────────┘   │
│  │  PTT btn │                                           │
│  │  LED     │    ┌──────────┐    ┌──────────────────┐   │
│  │  Debug   │<───│ Playback │<───│  Jitter Buffer   │<──┼──── Network <────
│  │  Panel   │    │ (PyAudio)│    │  (reorder/fill)  │   │
│  └──────────┘    └──────────┘    └──────────────────┘   │
│       │                                │                │
│       └──────── Metrics ───────────────┘                │
│                (packet loss, latency, throughput)        │
└─────────────────────────────────────────────────────────┘
```

---

## 3. File Breakdown

### 3.1 `jitter_buffer.py` - Packet Reordering Engine

**Why it exists:** UDP packets can arrive out of order (packet 5 before packet 3), get duplicated, or never arrive at all. The jitter buffer sits between the network and the speaker, smoothing out these issues.

#### Core Class: `JitterBuffer`

```python
buffer = JitterBuffer(capacity=3)  # Holds up to 3 packets (60ms of audio)
```

**Key attributes:**
- `self.buffer` - A sorted list of `(sequence, timestamp, audio_data)` tuples
- `self.capacity` - Max packets to hold (default 3 = 60ms at 20ms/frame)
- `self.last_played_seq` - Tracks the last sequence number sent to the speaker
- `self.lock` - Thread-safe access (multiple threads read/write the buffer)

#### Method: `add(packet)`

Called when a UDP packet arrives. Here's the decision logic:

```
Incoming packet (seq=7)
        │
        ▼
┌─ Is seq <= last_played_seq? ──── YES ──> DROP (too late, already played past it)
│       │ NO
│       ▼
├─ Is seq already in buffer? ───── YES ──> DROP (duplicate)
│       │ NO
│       ▼
├─ Append to buffer
├─ Sort buffer by sequence number
│       │
│       ▼
└─ Is buffer > capacity? ──────── YES ──> Pop oldest packet (lowest seq)
        │ NO
        ▼
      DONE
```

**Example scenario** - packets arrive as `[3, 5, 4]`:
1. Packet 3 arrives -> buffer: `[(3, ...)]`
2. Packet 5 arrives -> buffer: `[(3, ...), (5, ...)]`
3. Packet 4 arrives -> buffer: `[(3, ...), (4, ...), (5, ...)]` (sorted!)

#### Method: `get_next()`

Called by the playback thread every 20ms. Returns the next audio frame:

```
┌─ Is buffer empty? ───────────── YES ──> return None (silence, sleep)
│       │ NO
│       ▼
├─ Peek at first packet's seq
│       │
│       ▼
├─ Is seq > expected_seq? ─────── YES ──> return SILENCE (gap detected!)
│       │ NO                              (advance expected_seq by 1)
│       ▼
├─ Pop first packet
├─ Update last_played_seq
└─ return audio_data (160 bytes)
```

**Gap handling example** - last played was seq 3, buffer has `[(5, ...), (6, ...)]`:
- Expected seq = 4, but buffer starts at 5
- Returns silence for seq 4, advances to expect seq 5
- Next call: returns audio for seq 5

#### Helper Functions: `parse_packet()` and `create_packet()`

```python
# Creating a packet (sender side)
packet = create_packet(audio_bytes, sequence_number=42)
# Result: 8-byte header + 160-byte audio = 168 bytes total

# Parsing a packet (receiver side)
seq, timestamp, audio = parse_packet(raw_udp_data)
# seq = 42, timestamp = 1708900000123 (ms), audio = 160 bytes
```

The 8-byte header uses `struct.pack("!II", seq, timestamp)`:
- `!` = network byte order (big-endian)
- `I` = unsigned 32-bit integer (4 bytes each)

---

### 3.2 `metrics.py` - Network Statistics Tracker

**Why it exists:** The PRD requires a live debug overlay showing exactly what's happening on the network. This module collects and computes all the stats.

#### Core Class: `NetworkMetrics`

```python
metrics = NetworkMetrics()
```

**What it tracks:**

| Metric | How It's Calculated | Updated When |
|--------|-------------------|--------------|
| Sequence # | Highest seen send/recv seq | Every packet sent/received |
| Packet Loss % | `(highest_seq + 1 - recv_count) / (highest_seq + 1) * 100` | Every received packet |
| Throughput | `total_bytes / elapsed_time` | Every packet |
| Latency | `current_time_ms - packet_timestamp_ms` | Every received packet |
| Addresses | `local_ip:port -> peer_ip:port` | On connect |

#### Method: `record_sent(num_bytes)`

Called every time we send a packet. Simply increments the send counter and byte count:
```python
metrics.record_sent(160)  # Sent 160 bytes of audio
```

#### Method: `record_received(seq, timestamp, num_bytes)`

Called every time a packet arrives. Does three things:
1. Increments receive count + byte count
2. Updates highest sequence seen (for packet loss calculation)
3. Computes latency from the timestamp in the packet header

```python
metrics.record_received(seq=42, timestamp=1708900000123, num_bytes=160)
```

**Packet loss calculation:**
```
If we've seen packets with seq 0, 1, 2, 5, 6, 7:
  - highest_seq = 7
  - expected = 7 + 1 = 8
  - received = 6
  - loss = (8 - 6) / 8 * 100 = 25.0%
  (packets 3 and 4 were lost)
```

**Latency estimation:**
```
Packet carries: timestamp = 1708900000100 (ms when sender created it)
We receive it at: now = 1708900000135 (ms)
One-way latency estimate = 135 - 100 = 35ms
```
Note: This assumes clocks are roughly synchronized. Not perfect, but good enough for a debug display.

#### Method: `format_debug_line()`

Produces the monospace overlay string matching the PRD spec:
```
SEQ: 00001234 | LOSS: 2.3% THRU: 8.1 KB/s | LAT: 35ms BUFFER: 2/3
```

---

### 3.3 `ui.py` - The Full Interface

**Why it exists:** This is what the user sees and interacts with. It ties everything together: socket creation, audio capture, packet sending/receiving, jitter buffering, and metrics display.

#### UI Layout

```
┌────────────────────────────────────┐
│        UDP WALKIE-TALKIE           │  <- Red accent banner
├────────────────────────────────────┤
│  Connection Settings               │
│  Peer IP: [localhost         ]     │  <- Where to send audio
│  Port:    [5000]  Local: [5001]    │  <- Peer port + our bind port
│  [          Connect           ]    │  <- Toggles connect/disconnect
├────────────────────────────────────┤
│              (●)                   │  <- LED: gray/white/red/green
│        Status: Idle                │
│                                    │
│  ┌──────────────────────────────┐  │
│  │      PUSH TO TALK           │  │  <- Hold to transmit
│  │    (Hold SPACE or Click)    │  │
│  └──────────────────────────────┘  │
├────────────────────────────────────┤
│  Network Debug                     │
│  Seq #:        00000042            │
│  Packet Loss:  2.3%                │
│  Throughput:   8.1 KB/s            │
│  Buffer:       2/3 packets         │
│  Latency:      35 ms               │
│  Route:        0.0.0.0:5001 ->     │
│                localhost:5000       │
│ ┌────────────────────────────────┐ │
│ │SEQ: 00000042 | LOSS: 2.3% ... │ │  <- Green monospace overlay
│ └────────────────────────────────┘ │
└────────────────────────────────────┘
```

#### LED Status Colors

| Color | State | Meaning |
|-------|-------|---------|
| Gray `#888888` | Disconnected | Socket not open |
| White `#ffffff` | Idle | Connected, waiting |
| Red `#e74c3c` | Transmitting | PTT active, mic capturing |
| Green `#2ecc71` | Receiving | Incoming audio playing |

#### Connection Flow (what happens when you click Connect)

```
Click "Connect"
      │
      ▼
1. Read IP, ports from text fields
      │
      ▼
2. Create UDP socket: socket(AF_INET, SOCK_DGRAM)
   Bind to local port: sock.bind(('', 5001))
   Set timeout: sock.settimeout(0.5)
      │
      ▼
3. Open PyAudio streams (if available)
   - Input stream  (microphone, 8kHz, mono, 16-bit)
   - Output stream (speaker, same format)
      │
      ▼
4. Start background threads:
   - Receive thread  → listens for incoming packets
   - Playback thread → pulls from jitter buffer → speaker
      │
      ▼
5. Update UI:
   - Button changes to "Disconnect" (green)
   - PTT button enabled
   - LED turns white (idle)
   - Input fields locked
```

#### Push-to-Talk Flow

```
SPACE pressed (or PTT button held)
      │
      ▼
Start capture thread:
      │
      ├─ Loop while transmitting:
      │   │
      │   ├─ Read 80 samples (160 bytes) from mic
      │   ├─ create_packet(audio, seq_number)
      │   │   └─ Packs: [4-byte seq][4-byte timestamp][160-byte audio]
      │   ├─ sock.sendto(packet, (peer_ip, peer_port))
      │   ├─ seq_number += 1
      │   └─ metrics.record_sent(160)
      │
SPACE released
      │
      ▼
is_transmitting = False → capture thread exits loop
LED returns to white (idle)
```

#### Receive & Playback Flow (always running after connect)

```
Receive Thread (background):              Playback Thread (background):
      │                                          │
      ├─ Loop:                                   ├─ Loop:
      │   ├─ sock.recvfrom(1024)                 │   ├─ jitter_buffer.get_next()
      │   │   (blocks up to 0.5s)                │   │       │
      │   ├─ parse_packet(data)                  │   │       ├─ Has packet? → return audio
      │   │   └─ (seq, timestamp, audio)         │   │       ├─ Gap? → return silence
      │   ├─ jitter_buffer.add(...)              │   │       └─ Empty? → return None
      │   └─ metrics.record_received(...)        │   │
      │                                          │   ├─ If audio data:
      │                                          │   │   └─ output_stream.write(audio)
      │                                          │   └─ If None:
      │                                          │       └─ sleep(20ms)
```

#### Metrics Update Loop

The UI refreshes the debug panel every **100ms** using Tkinter's `root.after()`:

```python
def _update_metrics_display(self):
    # Pull latest stats
    summary = metrics.get_summary()
    buf_occ, buf_cap = jitter_buffer.get_occupancy()

    # Update all 6 debug labels
    # Update the green one-liner overlay

    # Schedule next update in 100ms
    self.root.after(100, self._update_metrics_display)
```

---

## 4. How the Packet Protocol Works

Every UDP datagram is exactly **168 bytes**:

```
 0                   4                   8                 168
 ├───────────────────┼───────────────────┼──────────────────┤
 │  Sequence Number  │    Timestamp      │   Audio Data     │
 │    (uint32)       │    (uint32)       │   (160 bytes)    │
 │  4 bytes          │  4 bytes          │   PCM samples    │
 └───────────────────┴───────────────────┴──────────────────┘
         └──────── Header (8 bytes) ────────┘
```

- **Sequence Number**: Starts at 0, increments by 1 per frame. Used by the jitter buffer to detect loss, reorder, and drop duplicates.
- **Timestamp**: Milliseconds since epoch (mod 2^32). Used to estimate network latency.
- **Audio Data**: Raw PCM audio - 80 samples at 16-bit = 160 bytes = 20ms of audio at 8kHz.

**Encoding** (sender):
```python
header = struct.pack("!II", sequence_number, timestamp)
packet = header + audio_data  # 8 + 160 = 168 bytes
```

**Decoding** (receiver):
```python
sequence, timestamp = struct.unpack("!II", packet[:8])
audio_data = packet[8:]  # 160 bytes
```

---

## 5. Data Flow: From Microphone to Speaker

Here's the complete journey of a single audio frame:

```
SENDER (Device A)                          RECEIVER (Device B)
─────────────────                          ──────────────────

1. Microphone picks up sound
         │
2. PyAudio reads 80 samples
   (160 bytes, 20ms of audio)
         │
3. create_packet(audio, seq=42)
   ┌──────────────────────┐
   │ seq=42 | ts=123 | PCM│  = 168 bytes
   └──────────────────────┘
         │
4. sock.sendto(packet, peer)
         │
    ═══════ UDP over network ═══════
         │
         │                          5. sock.recvfrom(1024)
         │                                 │
         │                          6. parse_packet(data)
         │                             → (42, 123, audio_bytes)
         │                                 │
         │                          7. jitter_buffer.add((42, 123, audio))
         │                             → sorted insert, check capacity
         │                                 │
         │                          8. metrics.record_received(42, 123, 160)
         │                             → update loss, latency, throughput
         │                                 │
         │                          9. jitter_buffer.get_next()
         │                             → returns audio for seq 42
         │                                 │
         │                         10. output_stream.write(audio)
         │                                 │
         │                         11. Speaker plays sound!
```

---

## 6. Threading Model

The app uses **4 threads** when fully active:

```
┌──────────────────────────────────────────────┐
│                MAIN THREAD                    │
│  - Tkinter event loop (mainloop)             │
│  - UI updates every 100ms                    │
│  - Handles button clicks, key presses        │
│  - NEVER blocked by network/audio            │
├──────────────────────────────────────────────┤
│           RECEIVE THREAD (daemon)             │
│  - Loops: sock.recvfrom() with 0.5s timeout  │
│  - Parses packets → jitter buffer            │
│  - Updates metrics                           │
│  - Runs entire time socket is open           │
├──────────────────────────────────────────────┤
│           PLAYBACK THREAD (daemon)            │
│  - Loops: jitter_buffer.get_next()           │
│  - Writes audio to speaker                   │
│  - Sleeps 20ms when buffer empty             │
│  - Runs entire time socket is open           │
├──────────────────────────────────────────────┤
│           CAPTURE THREAD (daemon)             │
│  - Only runs while PTT is held               │
│  - Reads mic → creates packet → sendto()     │
│  - New thread spawned each PTT press         │
│  - Dies when PTT released                    │
└──────────────────────────────────────────────┘
```

**Thread safety:** Both `JitterBuffer` and `NetworkMetrics` use `threading.Lock()` to protect shared state. The UI thread only reads (never writes) these objects, so there are no deadlock risks.

---

## 7. How to Connect Two Devices

### Option A: Same Machine (Testing)

Open **two terminal windows** and run two instances with **swapped ports**:

**Terminal 1:**
```bash
cd udp_walkie_talkie
python3 ui.py
# Set: Peer IP = localhost, Port = 5001, Local = 5000
# Click Connect
```

**Terminal 2:**
```bash
cd udp_walkie_talkie
python3 ui.py
# Set: Peer IP = localhost, Port = 5000, Local = 5001
# Click Connect
```

The port swap is critical - Instance 1 sends to port 5001 where Instance 2 is listening, and vice versa.

```
Instance 1 (local:5000)  ──sends to──>  Instance 2 (local:5001)
Instance 1 (local:5000)  <──sends to──  Instance 2 (local:5001)
```

### Option B: Two Machines on Same Network

1. Find each machine's IP:
   ```bash
   # macOS/Linux
   ifconfig | grep "inet "
   # Windows
   ipconfig
   ```

2. Make sure UDP port 5000 is not blocked by firewall.

**Machine A** (IP: 192.168.1.10):
```
Peer IP: 192.168.1.20
Port: 5000
Local: 5000
→ Connect
```

**Machine B** (IP: 192.168.1.20):
```
Peer IP: 192.168.1.10
Port: 5000
Local: 5000
→ Connect
```

3. Hold SPACE or click the PTT button to talk!

---

## 8. Setup & Installation

### Prerequisites

```bash
# Python 3.8+
python3 --version

# macOS - install PortAudio (required by PyAudio)
brew install portaudio

# Linux (Ubuntu/Debian)
sudo apt-get install portaudio19-dev

# Windows - download PyAudio wheel from Gohlke's repository
```

### Install Python packages

```bash
pip install pyaudio numpy
```

### Run the app

```bash
cd udp_walkie_talkie
python3 ui.py
```

> **Note:** The UI works even without PyAudio installed - it just won't capture/play audio. Useful for testing the UI and network logic.

---

## 9. Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| "Error - [Errno 48] Address already in use" | Port is taken by another process | Change the Local port, or kill the other process |
| No audio heard | Peer IP or port is wrong | Double-check both sides have correct IP/port and ports are swapped |
| Choppy audio | High network jitter or loss | Increase jitter buffer capacity (edit `capacity=3` to `capacity=5`) |
| PyAudio import error | PortAudio not installed | Run `brew install portaudio` (macOS) then `pip install pyaudio` |
| High packet loss in debug | Network congestion or firewall | Try wired connection, check firewall allows UDP on your port |
| UI freezes | Should not happen (threaded) | Report as bug - audio/network ops are on background threads |
| Latency shows 0ms | Both devices on localhost | Expected - there's no real network delay to measure |

---

## 10. Integration with Team Files

Our 3 files are designed to work standalone (the UI handles everything) **and** to integrate with the team's modules:

### What the team is building:

| File | Owner | Purpose |
|------|-------|---------|
| `main.py` | Team | Application entry point, wires everything together |
| `audio_handler.py` | Team | PyAudio capture/playback abstraction |
| `network_handler.py` | Team | UDP socket management, send/receive loops |

### How to integrate:

The team's `main.py` can import and use our modules directly:

```python
from jitter_buffer import JitterBuffer, parse_packet, create_packet
from metrics import NetworkMetrics
from ui import WalkieTalkieApp

# Create shared instances
buffer = JitterBuffer(capacity=3)
metrics = NetworkMetrics()

# Pass to UI or use in network_handler
```

If the team wants the UI to use their `audio_handler` and `network_handler` instead of the built-in socket/audio logic in `ui.py`, they can:

1. Subclass `WalkieTalkieApp` and override `_connect()`, `_capture_loop()`, `_receive_loop()`
2. Or simply pass their handler objects into the app constructor (we can add this if needed)

### API surface our files expose:

```python
# jitter_buffer.py
buffer = JitterBuffer(capacity=3)
buffer.add((seq, timestamp, audio_data))  # → bool
audio = buffer.get_next()                  # → bytes or None
occ, cap = buffer.get_occupancy()          # → (int, int)
stats = buffer.get_stats()                 # → dict
packet = create_packet(audio_data, seq)    # → bytes (168)
seq, ts, audio = parse_packet(raw_data)    # → tuple

# metrics.py
metrics = NetworkMetrics()
metrics.record_sent(num_bytes)
metrics.record_received(seq, timestamp, num_bytes)
loss = metrics.get_packet_loss()            # → float (%)
send_bps, recv_bps = metrics.get_throughput()
latency = metrics.get_latency()             # → float (ms)
summary = metrics.get_summary()             # → dict
debug_str = metrics.format_debug_line(occ, cap)  # → str

# ui.py
from ui import main
main()  # Launches the Tkinter window
```

---

*Built for the Computer Networks UDP Voice System project - February 2026*
