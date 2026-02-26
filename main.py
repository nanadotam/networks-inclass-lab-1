# main.py
import argparse
import threading
import time

from audio_handler import AudioHandler
from network_handler import NetworkHandler


def main():
    parser = argparse.ArgumentParser(description="Python UDP Walkie-Talkie (3-file version)")
    parser.add_argument("--local-port", type=int, default=5000, help="UDP port to bind/listen on")
    parser.add_argument("--peer-ip", type=str, required=True, help="Peer IP address")
    parser.add_argument("--peer-port", type=int, default=5000, help="Peer UDP port")
    parser.add_argument("--jb-capacity", type=int, default=3, help="Jitter buffer capacity (packets)")
    parser.add_argument("--print-metrics", action="store_true", help="Print live metrics to console")
    args = parser.parse_args()

    # Network + audio setup
    net = NetworkHandler(
        local_port=args.local_port,
        peer_ip=args.peer_ip,
        peer_port=args.peer_port,
        jitter_capacity=args.jb_capacity,
    )
    audio = AudioHandler()

    # Start receiver thread (UDP receive -> jitter buffer)
    net.start_receiver()

    # Start playback loop thread (jitter buffer -> speaker)
    playback_stop = threading.Event()

    def playback_loop():
        # Pull a frame roughly every 20ms and play (silence if missing)
        while not playback_stop.is_set():
            frame = net.get_playable_audio_frame()
            audio.play_frame(frame)
            # Keep timing close to 20ms
            time.sleep(audio.frame_duration_sec)

    playback_thread = threading.Thread(target=playback_loop, daemon=True)
    playback_thread.start()

    # Push-to-talk: simple console toggle (no extra libraries)
    # Type 't' then Enter to toggle transmit ON/OFF. Type 'q' then Enter to quit.
    print("\nUDP Walkie-Talkie")
    print(f"Listening on UDP :{args.local_port}")
    print(f"Sending to {args.peer_ip}:{args.peer_port}")
    print("Controls: 't' + Enter = toggle transmit | 'q' + Enter = quit\n")

    tx_enabled = False
    tx_stop = threading.Event()

    def capture_and_send_loop():
        # Read mic frames only while tx_enabled is True
        nonlocal tx_enabled
        while not tx_stop.is_set():
            if tx_enabled:
                frame = audio.capture_frame()
                net.send_audio_frame(frame)
            else:
                # Sleep lightly so we don't burn CPU
                time.sleep(0.01)

    tx_thread = threading.Thread(target=capture_and_send_loop, daemon=True)
    tx_thread.start()

    # Optional metrics printer
    metrics_stop = threading.Event()

    def metrics_loop():
        while not metrics_stop.is_set():
            m = net.get_metrics_snapshot()
            print(
                f"SEQ_TX={m['tx_seq']}  SEQ_RX_LAST={m['rx_last_seq']}  "
                f"LOSS={m['loss_percent']:.1f}%  THRU={m['throughput_kbps']:.2f} KB/s  "
                f"BUF={m['buffer_occupancy']}/{m['buffer_capacity']}  "
                f"LAT={m['latency_ms']:.0f} ms"
            )
            time.sleep(1.0)

    if args.print_metrics:
        threading.Thread(target=metrics_loop, daemon=True).start()

    # Command loop
    try:
        while True:
            cmd = input().strip().lower()
            if cmd == "t":
                tx_enabled = not tx_enabled
                print("TRANSMIT:", "ON 🔴" if tx_enabled else "OFF ⚪")
            elif cmd == "q":
                break
            else:
                print("Unknown command. Use 't' to toggle transmit, 'q' to quit.")
    finally:
        # Clean shutdown
        tx_stop.set()
        playback_stop.set()
        metrics_stop.set()
        net.close()
        audio.close()
        print("Exited cleanly.")


if __name__ == "__main__":
    main()