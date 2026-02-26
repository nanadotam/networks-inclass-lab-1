# audio_handler.py
import pyaudio


class AudioHandler:
    """
    Handles microphone capture and speaker playback using PyAudio.

    PRD-aligned defaults:
    - 8kHz
    - mono
    - 16-bit PCM
    - 20ms frames
    - payload = 160 bytes per frame in PRD; for 16-bit @ 8kHz, 20ms = 160 samples, i.e. 320 bytes.
      The PRD mixes "bytes vs samples" wording; we implement the *correct PCM sizing*:
      20ms @ 8000 samples/sec = 160 samples, each 2 bytes => 320 bytes per frame.
    """
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 8000

    FRAME_MS = 20
    SAMPLES_PER_FRAME = int(RATE * (FRAME_MS / 1000.0))  # 160 samples
    BYTES_PER_SAMPLE = 2  # int16
    FRAME_BYTES = SAMPLES_PER_FRAME * BYTES_PER_SAMPLE  # 320 bytes

    def __init__(self):
        self.p = pyaudio.PyAudio()

        # Input stream (mic)
        self.in_stream = self.p.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.SAMPLES_PER_FRAME,
        )

        # Output stream (speaker)
        self.out_stream = self.p.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            output=True,
            frames_per_buffer=self.SAMPLES_PER_FRAME,
        )

        self.frame_duration_sec = self.FRAME_MS / 1000.0

    def capture_frame(self) -> bytes:
        """Read exactly one 20ms frame from the microphone."""
        data = self.in_stream.read(self.SAMPLES_PER_FRAME, exception_on_overflow=False)
        # data length should be FRAME_BYTES (320 bytes)
        return data

    def play_frame(self, frame: bytes) -> None:
        """Play one frame to the speaker (expects bytes in int16 PCM)."""
        if not frame:
            frame = b"\x00" * self.FRAME_BYTES
        # If frame size is wrong, pad/trim safely
        if len(frame) < self.FRAME_BYTES:
            frame = frame + (b"\x00" * (self.FRAME_BYTES - len(frame)))
        elif len(frame) > self.FRAME_BYTES:
            frame = frame[: self.FRAME_BYTES]

        self.out_stream.write(frame)

    def silence_frame(self) -> bytes:
        return b"\x00" * self.FRAME_BYTES

    def close(self):
        try:
            self.in_stream.stop_stream()
            self.in_stream.close()
        except Exception:
            pass
        try:
            self.out_stream.stop_stream()
            self.out_stream.close()
        except Exception:
            pass
        try:
            self.p.terminate()
        except Exception:
            pass