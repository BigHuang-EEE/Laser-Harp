"""Laser harp controller for Raspberry Pi (polling version, clean Ctrl+C exit)."""

from __future__ import annotations

import importlib
import importlib.util
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence


# =========================
# 配置数据结构
# =========================

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


# =========================
# PWM 播放器
# =========================

class PWMNotePlayer:
    def __init__(self, gpio, speaker_pin: int, duty_cycle: float, note_duration: float):
        self.gpio = gpio
        self.speaker_pin = speaker_pin
        self.duty_cycle = duty_cycle
        self.note_duration = note_duration

        self._queue: "queue.Queue[float]" = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._running = threading.Event()

        # 喇叭引脚必须先设为输出，再创建 PWM
        self.gpio.setup(self.speaker_pin, self.gpio.OUT)
        self._pwm = self.gpio.PWM(self.speaker_pin, 440)

    def start(self) -> None:
        self._running.set()
        self._worker.start()

    def stop(self) -> None:
        self._running.clear()
        # 唤醒线程，避免一直阻塞在 get()
        try:
            self._queue.put_nowait(0.0)
        except queue.Full:
            pass
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


# =========================
# OLED 显示
# =========================

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
        for idx, line in enumerate(lines):
            y = idx * 12
            self._draw.text((0, y), line, font=self._font, fill=255)
        self._display.image(self._image)
        self._display.show()


# =========================
# 激光琴弦核心（轮询版）
# =========================

class LaserHarp:
    def __init__(self, config: LaserHarpConfig):
        self.config = config

        self.gpio = load_module("RPi.GPIO")
        self.gpio.setmode(self.gpio.BCM)
        self.gpio.setwarnings(False)

        self._note_by_receiver: Dict[int, NoteConfig] = {
            n.receiver_pin: n for n in self.config.notes
        }
        self._debug_index_by_receiver: Dict[int, int] = {
            n.receiver_pin: idx + 1 for idx, n in enumerate(self.config.notes)
        }

        self._melody_progress: List[str] = []
        self._note_player = PWMNotePlayer(
            self.gpio,
            speaker_pin=self.config.speaker_pin,
            duty_cycle=self.config.duty_cycle,
            note_duration=self.config.note_duration,
        )
        self._display: OLEDDisplay | None = None
        self._melody_thread = threading.Thread(target=self._melody_loop, daemon=True)
        self._melody_running = threading.Event()

        # 轮询需要记住每个接收脚的状态
        self._receiver_pins: List[int] = [n.receiver_pin for n in self.config.notes]
        self._last_states: Dict[int, int] = {}

    def setup(self) -> None:
        # 配置激光发射管 & 接收器
        for note in self.config.notes:
            # 激光：输出，高电平点亮
            self.gpio.setup(note.laser_pin, self.gpio.OUT)
            self.gpio.output(note.laser_pin, self.gpio.HIGH)

            # 接收：输入，上拉
            self.gpio.setup(note.receiver_pin, self.gpio.IN, pull_up_down=self.gpio.PUD_UP)
            self._last_states[note.receiver_pin] = self.gpio.input(note.receiver_pin)

        # 启动声音线程
        self._note_player.start()
        self._melody_running.set()
        self._melody_thread.start()

        # 初始化 OLED
        self._display = OLEDDisplay(self.config.oled_width, self.config.oled_height)
        self._display.show_lines(["Laser Harp Ready", "Break a beam..."])

    def loop(self) -> None:
        # 主循环：轮询接收脚，检测从 LOW -> HIGH 的变化
        while True:
            for pin in self._receiver_pins:
                state = self.gpio.input(pin)
                last = self._last_states[pin]

                if state != last:
                    self._last_states[pin] = state

                    # HIGH 视为激光照到接收器
                    if state == self.gpio.HIGH:
                        self._on_beam_hit(pin)

            time.sleep(0.01)

    def cleanup(self) -> None:
        # 尽量保证多次调用也不会出问题
        try:
            self._melody_running.clear()
            self._note_player.stop()
        except Exception:
            pass

        try:
            self.gpio.cleanup()
        except Exception:
            pass

    def _on_beam_hit(self, pin: int) -> None:
        note = self._note_by_receiver.get(pin)
        if not note:
            return

        debug_index = self._debug_index_by_receiver.get(pin)
        if debug_index is not None:
            print(debug_index)

        self._note_player.play_note(note.frequency)
        self._update_melody(note.name)

    def _update_melody(self, note_name: str) -> None:
        self._melody_progress.append(note_name)
        L = len(self.config.target_sequence)
        self._melody_progress = self._melody_progress[-L:]

        if tuple(self._melody_progress) == tuple(self.config.target_sequence):
            if self._display:
                self._display.show_lines(
                    ["Sequence found!", "Key: 4925,12546"]
                )

    def _melody_loop(self) -> None:
        # Mary Had a Little Lamb (E D C D E E E | D D D | E G G | E D C D E E E | E D D E D C)
        melody = [
            329.63, 293.66, 261.63, 293.66, 329.63, 329.63, 329.63,
            293.66, 293.66, 293.66,
            329.63, 392.00, 392.00,
            329.63, 293.66, 261.63, 293.66, 329.63, 329.63, 329.63,
            329.63, 293.66, 293.66, 329.63, 293.66, 261.63,
        ]
        while self._melody_running.is_set():
            for frequency in melody:
                if not self._melody_running.is_set():
                    break
                self._note_player.play_note(frequency)
                time.sleep(self.config.note_duration)


# =========================
# 工具函数 & main
# =========================

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
    ]
    return LaserHarpConfig(notes=notes, speaker_pin=17)


def main() -> None:
    harp = LaserHarp(default_config())
    harp.setup()
    try:
        harp.loop()
    except KeyboardInterrupt:
        print("\nStopping Laser Harp (Ctrl+C detected)...")
    finally:
        harp.cleanup()
        print("GPIO cleaned up. Bye.")


if __name__ == "__main__":
    main()
