"""Laser harp controller for Raspberry Pi (polling version, no edge interrupts)."""

from __future__ import annotations

import importlib
import importlib.util
import queue
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence


# ---------------------------------------------------------
# Data structures
# ---------------------------------------------------------

@dataclass(frozen=True)
class NoteConfig:
    name: str
    frequency: float
    laser_pin: int
    receiver_pin: int


@dataclass
class LaserHarpConfig:
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


# ---------------------------------------------------------
# PWM Note Player
# ---------------------------------------------------------

class PWMNotePlayer:
    def __init__(self, gpio, speaker_pin: int, duty_cycle: float, note_duration: float):
        self.gpio = gpio
        self.speaker_pin = speaker_pin
        self.duty_cycle = duty_cycle
        self.note_duration = note_duration
        self._queue: "queue.Queue[float]" = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._running = threading.Event()

        # 必须设置为输出
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


# ---------------------------------------------------------
# OLED Display
# ---------------------------------------------------------

class OLEDDisplay:
    def __init__(self, width: int, height: int):
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
            y = index * 12
            self._draw.text((0, y), line, font=self._font, fill=255)
        self._display.image(self._image)
        self._display.show()


# ---------------------------------------------------------
# Laser Harp Core (Polling version)
# ---------------------------------------------------------

class LaserHarp:
    def __init__(self, config: LaserHarpConfig):
        self.config = config
        self.gpio = load_module("RPi.GPIO")
        self.gpio.setmode(self.gpio.BCM)
        self.gpio.setwarnings(False)

        # 音符按接收脚查找
        self._note_by_receiver: Dict[int, NoteConfig] = {
            note.receiver_pin: note for note in self.config.notes
        }

        # 播放器 + OLED
        self._melody_progress: List[str] = []
        self._note_player = PWMNotePlayer(
            self.gpio,
            speaker_pin=self.config.speaker_pin,
            duty_cycle=self.config.duty_cycle,
            note_duration=self.config.note_duration,
        )
        self._display: OLEDDisplay | None = None

        # 轮询数据结构
        self._receiver_pins: List[int] = [n.receiver_pin for n in self.config.notes]
        self._last_states: Dict[int, int] = {}

    def setup(self) -> None:
        # 激光脚输出，高电平亮
        for note in self.config.notes:
            self.gpio.setup(note.laser_pin, self.gpio.OUT)
            self.gpio.output(note.laser_pin, self.gpio.HIGH)

            # 接收脚输入 + 上拉
            self.gpio.setup(note.receiver_pin, self.gpio.IN, pull_up_down=self.gpio.PUD_UP)
            self._last_states[note.receiver_pin] = self.gpio.input(note.receiver_pin)

        self._note_player.start()

        self._display = OLEDDisplay(self.config.oled_width, self.config.oled_height)
        self._display.show_lines(["Laser Harp Ready", "Break a beam..."])

    def loop(self) -> None:
        try:
            while True:
                for pin in self._receiver_pins:
                    state = self.gpio.input(pin)
                    last_state = self._last_states[pin]

                    # 状态变化时触发
                    if state != last_state:
                        self._last_states[pin] = state

                        # LOW 表示中断光束
                        if state == self.gpio.LOW:
                            self._on_beam_changed(pin)

                time.sleep(0.01)
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        self._note_player.stop()
        self.gpio.cleanup()

    def _on_beam_changed(self, pin: int) -> None:
        note = self._note_by_receiver.get(pin)
        if not note:
            return

        self._note_player.play_note(note.frequency)
        self._update_melody(note.name)

    def _update_melody(self, note_name: str) -> None:
        self._melody_progress.append(note_name)
        L = len(self.config.target_sequence)
        self._melody_progress = self._melody_progress[-L:]

        if tuple(self._melody_progress) == tuple(self.config.target_sequence):
            if self._display:
                self._display.show_lines([
                    "Sequence found!",
                    f"Key: {self.config.melody_key}"
                ])


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def load_module(module_name: str):
    if importlib.util.find_spec(module_name) is None:
        raise ModuleNotFoundError(
            f"Module '{module_name}' is required but was not found. Install it."
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
    harp = LaserHarp(default_config())
    harp.setup()
    harp.loop()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *args: None)
    main()
