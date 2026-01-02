# Laser-Harp

A Raspberry Pi Python script that turns three paired laser receivers plus a shared laser
emitter into an interactive harp. Hitting a receiver with its laser plays the matching **do / re / mi**
note, and playing the melody `mi re do re mi mi mi` shows the key on an
SSD1306 OLED display.

## Hardware

- Raspberry Pi with GPIO header
- 1 × laser diode output (wired to a GPIO output pin, shared across emitters)
- 3 × photodiode/phototransistor receivers (wired to GPIO input pins with pull-ups)
- Piezo speaker/buzzer on a PWM-capable GPIO pin
- SSD1306 I²C OLED display

### Default pin map

Laser emitter output: **BCM 5**

| Note | Frequency (Hz) | Receiver pin |
| --- | --- | --- |
| do | 261.63 | BCM 12 |
| re | 293.66 | BCM 16 |
| mi | 329.63 | BCM 20 |

The buzzer/speaker uses PWM on **BCM 17** by default. Adjust the pins in
`default_config()` inside `laser_harp.py` if your wiring differs.

## Software setup

Install required libraries on the Pi:

```
python3 -m venv ~/dianchuang-env
source ~/dianchuang-env/bin/activate
pip install luma.oled pillow
python laser_harp.py
```

## Running

1. Connection.
2. Run the controller:

   ```
   source ~/dianchuang-env/bin/activate
   python laser_harp.py
   ```

3. Break a beam to play its note. When the melody `mi re do re mi mi mi` is
   completed, the OLED will display `Sequence found!` and the key.
