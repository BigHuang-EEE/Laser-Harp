"""Laser harp controller for Raspberry Pi.

This script drives five laser emitters and five corresponding receivers.
Breaking a beam plays the associated solfege note (do, re, mi, fa, so).
When the sequence "mi re do re mi mi mi" is played, the OLED shows
"8848" as the key.
"""

from __future__ import annotations

import importlib
import importlib.util
import queue
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence


@dataclass(frozen=True)
class NoteConfig:
    """Mapping between a note, its GPIO pins, and playback frequency."""

    name: str
    frequency: float
    laser_pin: int
    receiver_pin: int


@dataclass
class LaserHarpConfig:
    """Top-level configuration for the laser harp."""

    notes: List[NoteConfig]
    speaker_pin: int
    target_sequence: Sequence[str] = field(
        default_factory=lambda: ("mi", "re", "do", "re", "mi", "mi", "mi")
    )
    melody_key: str = "8848"
    note_duration: float = 0.35
    duty_cycle: float = 50.0
    debounce_ms: int = 60
    oled_width: int = 128
    oled_height: int = 64


class PWMNotePlayer:
    """Queue-based PWM tone player to avoid blocking interrupt callbacks."""

    def __init__(self, gpio, speaker_pin: int, duty_cycle: float, note_duration: float):
        self.gpio = gpio
        self.speaker_pin = speaker_pin
        self.duty_cycle = duty_cycle
        self.note_duration = note_duration
        self._queue: "queue.Queue[float]" = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._running = threading.Event()

        self.gpio.setup(self.speaker_pin, self.gpio.OUT)
        self._pwm = self.gpio.PWM(self.speaker_pin, 440)

    def start(self) -> None:
        self._running.set()
        self._worker.start()

    def stop(self) -> None:
        self._running.clear()
        self._queue.put_nowait(0.0)
        self._worker.join(timeout=1.0)
        self._pwm.stop()

    def play_note(self, frequency: float) -> None:
        self._queue.put(frequency)

    def _worker_loop(self) -> None:
        while self._running.is_set():
            try:
                frequency = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if frequency > 0:
                self._pwm.ChangeFrequency(frequency)
                self._pwm.start(self.duty_cycle)
                time.sleep(self.note_duration)
                self._pwm.stop()

            self._queue.task_done()


class OLEDDisplay:
    """Minimal OLED helper for the SSD1306 display."""

    def __init__(self, width: int, height: int):
        digitalio = load_module("digitalio")
        board = load_module("board")
        adafruit_ssd1306 = load_module("adafruit_ssd1306")
        pil_image = load_module("PIL.Image")
        pil_draw = load_module("PIL.ImageDraw")
        pil_font = load_module("PIL.ImageFont")

        self.width = width
        self.height = height
        self._image = pil_image.new("1", (width, height))
        self._draw = pil_draw.Draw(self._image)
        self._font = pil_font.load_default()

        i2c = board.I2C()
        self._display = adafruit_ssd1306.SSD1306_I2C(width, height, i2c)

    def show_lines(self, lines: Iterable[str]) -> None:
        self._draw.rectangle((0, 0, self.width, self.height), outline=0, fill=0)
        for index, line in enumerate(lines):
            y_position = index * 12
            self._draw.text((0, y_position), line, font=self._font, fill=255)
        self._display.image(self._image)
        self._display.show()


class LaserHarp:
    """Core controller for lasers, receivers, audio, and display."""

    def __init__(self, config: LaserHarpConfig):
        self.config = config
        self.gpio = load_module("RPi.GPIO")
        self.gpio.setmode(self.gpio.BCM)
        self.gpio.setwarnings(False)

        self._note_by_receiver: Dict[int, NoteConfig] = {
            note.receiver_pin: note for note in self.config.notes
        }
        self._melody_progress: List[str] = []
        self._note_player = PWMNotePlayer(
            self.gpio,
            speaker_pin=self.config.speaker_pin,
            duty_cycle=self.config.duty_cycle,
            note_duration=self.config.note_duration,
        )
        self._display: OLEDDisplay | None = None

    def setup(self) -> None:
        for note in self.config.notes:
            self.gpio.setup(note.laser_pin, self.gpio.OUT)
            self.gpio.output(note.laser_pin, self.gpio.HIGH)

            self.gpio.setup(note.receiver_pin, self.gpio.IN, pull_up_down=self.gpio.PUD_UP)
            self.gpio.add_event_detect(
                note.receiver_pin,
                self.gpio.BOTH,
                callback=self._on_beam_changed,
                bouncetime=self.config.debounce_ms,
            )

        self._note_player.start()
        self._display = OLEDDisplay(self.config.oled_width, self.config.oled_height)
        self._display.show_lines(["Laser Harp Ready", "Break a beam..."])

    def loop(self) -> None:
        try:
            while True:
                time.sleep(0.25)
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        self._note_player.stop()
        self.gpio.cleanup()

    def _on_beam_changed(self, channel: int) -> None:
        state = self.gpio.input(channel)
        if state == self.gpio.HIGH:
            return

        note = self._note_by_receiver.get(channel)
        if not note:
            return

        self._note_player.play_note(note.frequency)
        self._update_melody(note.name)

    def _update_melody(self, note_name: str) -> None:
        self._melody_progress.append(note_name)
        target_length = len(self.config.target_sequence)
        self._melody_progress = self._melody_progress[-target_length:]

        if tuple(self._melody_progress) == tuple(self.config.target_sequence):
            if self._display:
                self._display.show_lines(["Sequence found!", f"Key: {self.config.melody_key}"])


def load_module(module_name: str):
    """Import a module after confirming it exists on the system."""

    if importlib.util.find_spec(module_name) is None:
        raise ModuleNotFoundError(
            f"Module '{module_name}' is required but was not found. Install it on the Raspberry Pi."
        )
    return importlib.import_module(module_name)


def default_config() -> LaserHarpConfig:
    notes = [
        NoteConfig("do", 261.63, laser_pin=5, receiver_pin=12),
        NoteConfig("re", 293.66, laser_pin=6, receiver_pin=16),
        NoteConfig("mi", 329.63, laser_pin=13, receiver_pin=20),
        NoteConfig("fa", 349.23, laser_pin=19, receiver_pin=21),
        NoteConfig("so", 392.00, laser_pin=26, receiver_pin=18),
    ]
    return LaserHarpConfig(notes=notes, speaker_pin=17)


def main() -> None:
    config = default_config()
    harp = LaserHarp(config)
    harp.setup()
    harp.loop()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *args: None)
    main()
