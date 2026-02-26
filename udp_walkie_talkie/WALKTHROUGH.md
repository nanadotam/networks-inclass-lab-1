# UDP Walkie-Talkie - Technical Walkthrough

> A real-time push-to-talk voice communication system built with Python, UDP sockets, and Tkinter.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture at a Glance](#2-architecture-at-a-glance)
3. [File Breakdown](#3-file-breakdown)
   - [jitter_buffer.py](#31-jitter_bufferpy---packet-reordering-engine)
   - [metrics.py](#32-metricspy---network-statistics-tracker)
   - [ui.py](#33-uipy---the-full-gui-interface)
   - [audio_handler.py](#34-audio_handlerpy---pyaudio-wrapper)
   - [network_handler.py](#35-network_handlerpy---udp-socket-manager)
   - [main.py](#36-mainpy---cli-entry-point)
4. [How the Packet Protocol Works](#4-how-the-packet-protocol-works)
5. [Data Flow: From Microphone to Speaker](#5-data-flow-from-microphone-to-speaker)
6. [Threading Model](#6-threading-model)
7. [How to Connect Two Devices](#7-how-to-connect-two-devices)
8. [Setup & Installation](#8-setup--installation)
9. [Running the App](#9-running-the-app)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Project Overview

This is a **UDP-based walkie-talkie** app. Unlike TCP (which guarantees delivery), UDP is *fire-and-forget* - packets may arrive out of order, get duplicated, or be lost entirely. That's actually fine for voice: a slightly garbled syllable is better than waiting 200ms for a retransmission.

The app captures audio from your microphone, chops it into 20ms frames, wraps each frame in a small header (sequence number + timestamp), and fires it over UDP to a peer. The peer's jitter buffer reorders any out-of-order packets, fills in silence for lost ones, and feeds them to the speaker.

**The app can run in two modes:**

| Mode | Entry Point | Interface | Description |
|------|------------|-----------|-------------|
| **GUI Mode** | `python3 ui.py` | Tkinter window | Full graphical interface with PTT button, LED status, debug panel |
| **CLI Mode** | `python3 main.py --peer-ip <IP>` | Terminal | Lightweight console mode, type 't' to toggle transmit |

**All 6 files and their responsibilities:**

| File | Responsibility |
|------|---------------|
| `jitter_buffer.py` | Receives packets, reorders them, handles loss, packet create/parse |
| `metrics.py` | Tracks packet loss, throughput, latency in real-time |
| `ui.py` | Tkinter GUI with PTT button, LED status, debug panel |
| `audio_handler.py` | PyAudio capture/playback abstraction |
| `network_handler.py` | UDP socket management with integrated jitter buffer and metrics |
| `main.py` | CLI entry point wiring network + audio together |

---

## 2. Architecture at a Glance

```
+-------------------------------------------------------------+
|                        DEVICE A                              |
|                                                              |
|  +----------+    +------------+    +--------------------+    |
|  |   UI     |--->| AudioHandler|-->|  UDP Socket        |----+--- Network --->
|  | (Tkinter)|    | (PyAudio)  |   |  sendto()          |    |
|  |  or CLI  |    +------------+   +--------------------+    |
|  |          |                                                |
|  |  PTT btn |    +------------+    +--------------------+    |
|  |  LED     |<---| AudioHandler|<--|  Jitter Buffer     |<---+--- Network <---
|  |  Debug   |    | (PyAudio)  |   |  (reorder/fill)    |    |
|  |  Panel   |    +------------+   +--------------------+    |
|  +----------+                            |                   |
|       |                                  |                   |
|       +--------- NetworkMetrics ---------+                   |
|               (packet loss, latency, throughput)             |
+--------------------------------------------------------------+
```

---

## 3. File Breakdown

### 3.1 `jitter_buffer.py` - Packet Reordering Engine

**Why it exists:** UDP packets can arrive out of order (packet 5 before packet 3), get duplicated, or never arrive at all. The jitter buffer sits between the network and the speaker, smoothing out these issues.

#### Constants

```python
SAMPLES_PER_FRAME = 160   # 20ms @ 8kHz
BYTES_PER_SAMPLE = 2      # 16-bit PCM
CHUNK = 320                # 160 samples * 2 bytes = 320 bytes per frame
SILENCE = b'\x00' * 320   # Silence frame for lost packets
```

#### Core Class: `JitterBuffer`

```python
buffer = JitterBuffer(capacity=3)  # Holds up to 3 packets (60ms of audio)
```

**Key attributes:**
- `self.buffer` - A sorted list of `(sequence, timestamp, audio_data)` tuples
- `self.capacity` - Max packets to hold (default 3 = 60ms at 20ms/frame)
- `self.last_played_seq` - Tracks the last sequence number sent to the speaker
- `self.lock` - Thread-safe access (multiple threads read/write the buffer)
- `self.total_added`, `total_dropped`, `total_late`, `total_duplicate` - Stats counters

#### Method: `add(packet)`

Called when a UDP packet arrives. Takes a tuple `(seq, timestamp, audio_data)`:

```
Incoming packet (seq=7)
        |
        v
+- Is seq <= last_played_seq? ---- YES --> DROP (too late, already played past it)
|       | NO
|       v
+- Is seq already in buffer? ----- YES --> DROP (duplicate)
|       | NO
|       v
+- Append to buffer
+- Sort buffer by sequence number
|       |
|       v
+- Is buffer > capacity? ---------- YES --> Pop oldest packet (lowest seq)
        | NO
        v
      DONE (returns True)
```

#### Method: `get_next()`

Called by the playback thread every 20ms. Returns audio bytes, SILENCE, or None:

```
+- Is buffer empty? --------------- YES --> return None (caller sleeps 20ms)
|       | NO
|       v
+- Peek at first packet's seq
|       |
|       v
+- Is seq > expected_seq? --------- YES --> return SILENCE (gap detected!)
|       | NO                                (advance last_played_seq by 1)
|       v
+- Pop first packet
+- Update last_played_seq
+- return audio_data (320 bytes)
```

#### Helper Functions: `parse_packet()` and `create_packet()`

```python
# Creating a packet (sender side)
packet = create_packet(audio_bytes, sequence_number=42)
# Result: 8-byte header + 320-byte audio = 328 bytes total

# Parsing a packet (receiver side)
seq, timestamp, audio = parse_packet(raw_udp_data)
# seq = 42, timestamp = 1708900000123 (ms), audio = 320 bytes
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

#### Key Methods

```python
metrics.record_sent(320)                                    # Track a sent packet
metrics.record_received(seq=42, timestamp=170890123, num_bytes=320)  # Track received
metrics.get_packet_loss()      # -> float (%)
metrics.get_throughput()       # -> (send_bps, recv_bps)
metrics.get_latency()          # -> float (ms)
metrics.get_summary()          # -> dict with all metrics
metrics.format_debug_line(2, 3)  # -> "SEQ: 00000042 | LOSS: 2.3% ..."
```

#### Packet Loss Calculation

```
If we've seen packets with seq 0, 1, 2, 5, 6, 7:
  - highest_seq = 7
  - expected = 7 + 1 = 8
  - received = 6
  - loss = (8 - 6) / 8 * 100 = 25.0%
  (packets 3 and 4 were lost)
```

---

### 3.3 `ui.py` - The Full GUI Interface

**Why it exists:** This is what the user sees and interacts with. It ties everything together using `jitter_buffer.py` and `metrics.py` directly, managing its own UDP socket and PyAudio streams.

#### UI Layout

```
+------------------------------------+
|        UDP WALKIE-TALKIE           |  <- Red accent banner
+------------------------------------+
|  Connection Settings               |
|  Peer IP: [localhost         ]     |
|  Port:    [5000]  Local: [5001]    |
|  [          Connect           ]    |
+------------------------------------+
|              (*)                   |  <- LED: gray/white/red/green
|        Status: Idle                |
|                                    |
|  +------------------------------+  |
|  |      PUSH TO TALK           |  |  <- Hold to transmit
|  |    (Hold SPACE or Click)    |  |
|  +------------------------------+  |
+------------------------------------+
|  Network Debug                     |
|  Seq #:        00000042            |
|  Packet Loss:  2.3%                |
|  Throughput:   8.1 KB/s            |
|  Buffer:       2/3 packets         |
|  Latency:      35 ms               |
|  Route:        0.0.0.0:5001 ->     |
|                localhost:5000       |
| +--------------------------------+ |
| |SEQ: 00000042 | LOSS: 2.3% ... | |  <- Green monospace overlay
| +--------------------------------+ |
+------------------------------------+
```

#### LED Status Colors

| Color | State | Meaning |
|-------|-------|---------|
| Gray `#888888` | Disconnected | Socket not open |
| White `#ffffff` | Idle | Connected, waiting |
| Red `#e74c3c` | Transmitting | PTT active, mic capturing |
| Green `#2ecc71` | Receiving | Incoming audio playing |

#### Connection Flow

1. Read IP and ports from text fields
2. Create UDP socket: `socket(AF_INET, SOCK_DGRAM)`, bind to local port
3. Open PyAudio input/output streams (8kHz, mono, 16-bit, 160 samples/frame)
4. Start receive thread + playback thread
5. Update UI: button changes to "Disconnect", PTT enabled, LED turns white

#### Push-to-Talk

- Hold SPACE or click PTT button to transmit
- Spawns a capture thread that reads mic -> `create_packet()` -> `sendto()`
- Release to stop, LED returns to white

---

### 3.4 `audio_handler.py` - PyAudio Wrapper

**Used by:** `main.py` (CLI mode)

Wraps PyAudio for clean mic capture and speaker playback:

```python
audio = AudioHandler()

# Capture one 20ms frame from microphone
frame = audio.capture_frame()   # -> 320 bytes

# Play one frame to speaker (pads/trims if needed)
audio.play_frame(frame)

# Get a silence frame
silence = audio.silence_frame()  # -> 320 zero bytes

audio.close()  # Clean shutdown
```

**Audio format:** 8kHz, mono, 16-bit PCM, 160 samples (320 bytes) per 20ms frame.

---

### 3.5 `network_handler.py` - UDP Socket Manager

**Used by:** `main.py` (CLI mode)

Integrates UDP socket management with `JitterBuffer` and `NetworkMetrics`:

```python
net = NetworkHandler(local_port=5000, peer_ip="192.168.1.20", peer_port=5000)
net.start_receiver()                      # Start background receive thread
net.send_audio_frame(audio_bytes)         # Send a 320-byte frame
frame = net.get_playable_audio_frame()    # Get next frame (or empty bytes)
snapshot = net.get_metrics_snapshot()      # Get metrics dict
net.close()                               # Clean shutdown
```

Internally uses `jitter_buffer.py` for buffering and `metrics.py` for statistics - no duplicated logic.

---

### 3.6 `main.py` - CLI Entry Point

**Used for:** headless/terminal operation without the Tkinter GUI.

Wires `NetworkHandler` + `AudioHandler` together with console-based push-to-talk:

```
+-- main thread:      input() loop, 't' to toggle TX, 'q' to quit
+-- receiver thread:  UDP recv -> jitter buffer (started by NetworkHandler)
+-- playback thread:  jitter buffer -> speaker (via AudioHandler)
+-- capture thread:   mic -> UDP send (toggleable)
+-- metrics thread:   optional, prints stats every 1s (--print-metrics)
```

---

## 4. How the Packet Protocol Works

Every UDP datagram is exactly **328 bytes**:

```
 0                   4                   8                 328
 +-------------------+-------------------+------------------+
 |  Sequence Number  |    Timestamp      |   Audio Data     |
 |    (uint32)       |    (uint32)       |   (320 bytes)    |
 |  4 bytes          |  4 bytes          |   PCM samples    |
 +-------------------+-------------------+------------------+
         +-------- Header (8 bytes) --------+
```

- **Sequence Number**: Starts at 0, increments by 1 per frame. Used by the jitter buffer to detect loss, reorder, and drop duplicates.
- **Timestamp**: Milliseconds since epoch (mod 2^32). Used to estimate network latency.
- **Audio Data**: Raw PCM audio - 160 samples at 16-bit = 320 bytes = 20ms of audio at 8kHz.

**Encoding** (sender):
```python
header = struct.pack("!II", sequence_number, timestamp)
packet = header + audio_data  # 8 + 320 = 328 bytes
```

**Decoding** (receiver):
```python
sequence, timestamp = struct.unpack("!II", packet[:8])
audio_data = packet[8:]  # 320 bytes
```

---

## 5. Data Flow: From Microphone to Speaker

```
SENDER (Device A)                          RECEIVER (Device B)
-----------------                          ------------------

1. Microphone picks up sound
         |
2. PyAudio reads 160 samples
   (320 bytes, 20ms of audio)
         |
3. create_packet(audio, seq=42)
   +----------------------+
   | seq=42 | ts=123 | PCM|  = 328 bytes
   +----------------------+
         |
4. sock.sendto(packet, peer)
         |
    ======= UDP over network =======
         |
         |                          5. sock.recvfrom(65535)
         |                                 |
         |                          6. parse_packet(data)
         |                             -> (42, 123, audio_bytes)
         |                                 |
         |                          7. jitter_buffer.add((42, 123, audio))
         |                             -> sorted insert, check capacity
         |                                 |
         |                          8. metrics.record_received(42, 123, 320)
         |                             -> update loss, latency, throughput
         |                                 |
         |                          9. jitter_buffer.get_next()
         |                             -> returns audio for seq 42
         |                                 |
         |                         10. output_stream.write(audio)
         |                                 |
         |                         11. Speaker plays sound!
```

---

## 6. Threading Model

The app uses **4 threads** when fully active:

```
+----------------------------------------------+
|                MAIN THREAD                    |
|  - Tkinter event loop (GUI) or input() (CLI) |
|  - UI updates every 100ms (GUI mode)         |
|  - Handles button clicks, key presses        |
|  - NEVER blocked by network/audio            |
+----------------------------------------------+
|           RECEIVE THREAD (daemon)             |
|  - Loops: sock.recvfrom() with timeout       |
|  - Parses packets -> jitter buffer           |
|  - Updates metrics                           |
|  - Runs entire time socket is open           |
+----------------------------------------------+
|           PLAYBACK THREAD (daemon)            |
|  - Loops: jitter_buffer.get_next()           |
|  - Writes audio to speaker                   |
|  - Sleeps 20ms when buffer empty             |
|  - Runs entire time socket is open           |
+----------------------------------------------+
|           CAPTURE THREAD (daemon)             |
|  - Only runs while PTT is held (GUI)         |
|    or TX toggled on (CLI)                     |
|  - Reads mic -> creates packet -> sendto()   |
|  - Dies when PTT released / TX toggled off   |
+----------------------------------------------+
```

**Thread safety:** Both `JitterBuffer` and `NetworkMetrics` use `threading.Lock()` to protect shared state. The UI thread only reads (never writes) these objects, so there are no deadlock risks.

---

## 7. How to Connect Two Devices

### Option A: Same Machine (Testing)

Open **two terminal windows** and run two instances with **swapped ports**:

**Terminal 1 (GUI):**
```bash
cd udp_walkie_talkie
python3 ui.py
# Set: Peer IP = localhost, Port = 5001, Local = 5000
# Click Connect
```

**Terminal 2 (GUI):**
```bash
cd udp_walkie_talkie
python3 ui.py
# Set: Peer IP = localhost, Port = 5000, Local = 5001
# Click Connect
```

**Or using CLI mode:**

**Terminal 1:**
```bash
cd udp_walkie_talkie
python3 main.py --peer-ip localhost --peer-port 5001 --local-port 5000
```

**Terminal 2:**
```bash
cd udp_walkie_talkie
python3 main.py --peer-ip localhost --peer-port 5000 --local-port 5001
```

The port swap is critical - Instance 1 sends to the port where Instance 2 is listening, and vice versa.

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
```bash
python3 main.py --peer-ip 192.168.1.20 --peer-port 5000 --local-port 5000
```

**Machine B** (IP: 192.168.1.20):
```bash
python3 main.py --peer-ip 192.168.1.10 --peer-port 5000 --local-port 5000
```

3. Hold SPACE or press 't' + Enter (CLI) to talk!

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

# Windows - PyAudio usually installs via pip directly
```

### Install Python packages

```bash
pip install pyaudio
```

---

## 9. Running the App

### GUI Mode (recommended)

```bash
cd udp_walkie_talkie
python3 ui.py
```

The GUI works even without PyAudio installed - it just won't capture/play audio. Useful for testing the UI and network logic.

### CLI Mode

```bash
cd udp_walkie_talkie

# Basic usage
python3 main.py --peer-ip localhost --peer-port 5001 --local-port 5000

# With live metrics printing
python3 main.py --peer-ip localhost --peer-port 5001 --local-port 5000 --print-metrics

# Custom jitter buffer size
python3 main.py --peer-ip localhost --peer-port 5001 --local-port 5000 --jb-capacity 5
```

**CLI Controls:**
- `t` + Enter = toggle transmit ON/OFF
- `q` + Enter = quit

### Quick Test (two instances on same machine)

Open two terminals side by side:

```bash
# Terminal 1
cd udp_walkie_talkie
python3 main.py --peer-ip localhost --peer-port 5001 --local-port 5000 --print-metrics

# Terminal 2
cd udp_walkie_talkie
python3 main.py --peer-ip localhost --peer-port 5000 --local-port 5001 --print-metrics
```

Type `t` + Enter in either terminal to start transmitting. Speak into your mic and hear it on the other instance.

---

## 10. Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: audio_handler` | File was named `audio_rename.py` | Ensure the file is named `audio_handler.py` |
| `[Errno 48] Address already in use` | Port is taken by another process | Change the Local port, or kill the other process |
| No audio heard | Peer IP or port is wrong | Double-check both sides have correct IP/port and ports are swapped |
| Choppy audio | High network jitter or loss | Increase jitter buffer capacity (`--jb-capacity 5` or edit code) |
| PyAudio import error | PortAudio not installed | Run `brew install portaudio` (macOS) then `pip install pyaudio` |
| High packet loss in debug | Network congestion or firewall | Try wired connection, check firewall allows UDP on your port |
| UI freezes | Should not happen (threaded) | Report as bug - audio/network ops are on background threads |
| Latency shows 0ms | Both devices on localhost | Expected - there's no real network delay to measure |

---

## API Summary

```python
# jitter_buffer.py
from jitter_buffer import JitterBuffer, create_packet, parse_packet, CHUNK, SILENCE, SAMPLES_PER_FRAME

buffer = JitterBuffer(capacity=3)
buffer.add((seq, timestamp, audio_data))   # -> bool
audio = buffer.get_next()                  # -> bytes, SILENCE, or None
occ, cap = buffer.get_occupancy()          # -> (int, int)
stats = buffer.get_stats()                 # -> dict
buffer.clear()                             # reset state
packet = create_packet(audio_data, seq)    # -> bytes (328)
seq, ts, audio = parse_packet(raw_data)    # -> tuple

# metrics.py
from metrics import NetworkMetrics

metrics = NetworkMetrics()
metrics.record_sent(num_bytes)
metrics.record_received(seq, timestamp, num_bytes)
metrics.set_addresses(local_addr, peer_addr)
loss = metrics.get_packet_loss()                    # -> float (%)
send_bps, recv_bps = metrics.get_throughput()       # -> (float, float)
latency = metrics.get_latency()                     # -> float (ms)
summary = metrics.get_summary()                     # -> dict
debug_str = metrics.format_debug_line(occ, cap)     # -> str

# network_handler.py
from network_handler import NetworkHandler

net = NetworkHandler(local_port, peer_ip, peer_port, jitter_capacity=3)
net.start_receiver()
net.send_audio_frame(audio_bytes)
frame = net.get_playable_audio_frame()   # -> bytes (320) or b""
snapshot = net.get_metrics_snapshot()     # -> dict
net.close()

# audio_handler.py
from audio_handler import AudioHandler

audio = AudioHandler()
frame = audio.capture_frame()    # -> bytes (320)
audio.play_frame(frame)
silence = audio.silence_frame()  # -> bytes (320)
audio.close()

# ui.py
from ui import main
main()  # Launches the Tkinter window
```

---

*Built for the Computer Networks UDP Voice System project - February 2026*
