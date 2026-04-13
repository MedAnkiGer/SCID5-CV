"""Interview Engine — TTS question delivery, audio recording, and Whisper transcription.

For each SCID-5-CV criterion:
  1. speak_text()        — AI reads question aloud (OpenAI TTS, never saved to disk)
  2. record_blocking()   — records patient's verbal answer (silence-detection)
  3. transcribe_audio()  — sends WAV bytes to Whisper API

Audio is never saved to disk (privacy).
"""

import io
import os
import time
import wave
from pathlib import Path


import numpy as np
import sounddevice as sd
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# Audio settings
SAMPLE_RATE = 16000   # 16kHz mono — optimal for Whisper
CHANNELS = 1
DTYPE = "int16"
MAX_DURATION_S = 300  # Maximum recording duration (seconds)
SILENCE_THRESHOLD_RMS = 300   # RMS amplitude below which is considered silence
SILENCE_DURATION_S = 8.0      # Seconds of silence before auto-stop
BLOCK_SIZE = 1024             # Frames per callback block

# TTS settings
TTS_SAMPLE_RATE = 24000   # OpenAI PCM output is 24kHz
TTS_MODEL = "tts-1"
TTS_VOICE_EN = "nova"
TTS_VOICE_DE = "nova"     # swap to another voice when DE is ready


def speak_text(text: str, language: str = "en") -> None:
    """Read text aloud using OpenAI TTS. Blocks until playback finishes.

    Uses raw PCM output for direct playback — no file is written to disk.

    Args:
        text: Text to speak.
        language: 'en' or 'de' (selects voice).
    """
    if not text or not text.strip():
        return

    voice = TTS_VOICE_DE if language == "de" else TTS_VOICE_EN
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # response_format="pcm" → raw signed 16-bit PCM at 24000 Hz, mono
    response = client.audio.speech.create(
        model=TTS_MODEL,
        voice=voice,
        input=text,
        response_format="pcm",
    )

    pcm_bytes = response.content
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    sd.play(audio, samplerate=TTS_SAMPLE_RATE)
    sd.wait()


class AudioRecorder:
    """Records audio with silence detection and provides WAV bytes."""

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        max_duration: int = MAX_DURATION_S,
        silence_threshold: int = SILENCE_THRESHOLD_RMS,
        silence_duration: float = SILENCE_DURATION_S,
        require_speech_first: bool = False,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_duration = max_duration
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.require_speech_first = require_speech_first

        self._frames: list[np.ndarray] = []
        self._is_recording = False
        self._silence_start: float | None = None
        self._stopped_by_silence = False
        self._speech_detected = False

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def stopped_by_silence(self) -> bool:
        return self._stopped_by_silence

    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice for each audio block during recording."""
        if not self._is_recording:
            return

        self._frames.append(indata.copy())

        # Check RMS for silence detection
        rms = np.sqrt(np.mean(indata.astype(np.float32) ** 2))

        if rms >= self.silence_threshold:
            self._speech_detected = True
            self._silence_start = None
        elif not self.require_speech_first or self._speech_detected:
            if self._silence_start is None:
                self._silence_start = time.time()
            elif time.time() - self._silence_start >= self.silence_duration:
                self._stopped_by_silence = True
                self._is_recording = False

    def start_recording(self) -> None:
        """Begin recording audio from the default input device."""
        self._frames = []
        self._is_recording = True
        self._silence_start = None
        self._stopped_by_silence = False
        self._speech_detected = False

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=DTYPE,
            blocksize=BLOCK_SIZE,
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop_recording(self) -> None:
        """Stop recording."""
        self._is_recording = False
        if hasattr(self, "_stream"):
            self._stream.stop()
            self._stream.close()

    def record_blocking(self) -> np.ndarray:
        """Record until silence or max duration. Blocks the calling thread.

        Returns:
            numpy array of recorded audio samples.
        """
        self.start_recording()
        start_time = time.time()
        print("  [Recording... speak now. Stops after silence.]", flush=True)

        try:
            while self._is_recording:
                time.sleep(0.1)
                if time.time() - start_time >= self.max_duration:
                    break
        finally:
            self.stop_recording()

        duration = round(time.time() - start_time, 1)
        print(f"  [Recording stopped — {duration}s captured]", flush=True)

        if not self._frames:
            return np.array([], dtype=np.int16)

        return np.concatenate(self._frames, axis=0)

    def get_wav_bytes(self, audio_data: np.ndarray | None = None) -> bytes:
        """Convert recorded audio to in-memory WAV bytes.

        Args:
            audio_data: Numpy array of audio samples. If None, uses last recording.

        Returns:
            WAV file content as bytes.
        """
        if audio_data is None:
            if not self._frames:
                return b""
            audio_data = np.concatenate(self._frames, axis=0)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_data.tobytes())

        buf.seek(0)
        return buf.read()

    @property
    def duration_seconds(self) -> float:
        """Duration of the last recording in seconds."""
        if not self._frames:
            return 0.0
        total_frames = sum(f.shape[0] for f in self._frames)
        return total_frames / self.sample_rate


def transcribe_audio_bytes(audio_bytes: bytes, ext: str = "webm", language: str = "de") -> str:
    """Send audio bytes (any format Whisper supports) for transcription.

    Args:
        audio_bytes: Raw audio file content.
        ext: File extension hint for Whisper (webm, ogg, wav, mp3, etc.).
        language: Language code ('de' or 'en').

    Returns:
        Transcribed text string.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = f"recording.{ext}"
    prompt = (
        "This is a clinical interview response. The patient may answer with numbers, "
        "short phrases, or brief sentences."
    )
    response = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        language=language,
        response_format="text",
        prompt=prompt,
    )
    return response.strip()


def transcribe_audio(wav_bytes: bytes, language: str = "de") -> str:
    """Send WAV audio bytes to OpenAI Whisper API for transcription.

    Args:
        wav_bytes: In-memory WAV file content.
        language: Language code ('de' or 'en').

    Returns:
        Transcribed text string.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Wrap bytes as a file-like object with a name attribute
    audio_file = io.BytesIO(wav_bytes)
    audio_file.name = "recording.wav"

    response = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        language=language,
        response_format="text",
    )

    return response.strip()


def record_and_transcribe(language: str = "de") -> dict:
    """Record audio and transcribe it. Main entry point for Stage 2.

    Args:
        language: Language code for Whisper.

    Returns:
        dict with keys: transcript (str), duration_s (float), stopped_by_silence (bool).
    """
    recorder = AudioRecorder()
    audio_data = recorder.record_blocking()

    if len(audio_data) == 0:
        return {
            "transcript": "",
            "duration_s": 0.0,
            "stopped_by_silence": False,
        }

    wav_bytes = recorder.get_wav_bytes(audio_data)
    print("  [Transcribing...]", flush=True)
    transcript = transcribe_audio(wav_bytes, language=language)
    print(f"  [Transcript]: {transcript}", flush=True)

    return {
        "transcript": transcript,
        "duration_s": recorder.duration_seconds,
        "stopped_by_silence": recorder.stopped_by_silence,
    }
