# Laser-Harp

A Raspberry Pi Python script that turns five paired laser emitters/receivers into
an interactive harp. Breaking a beam plays the matching **do / re / mi / fa / so**
note, and playing the melody `mi re do re mi mi mi` shows the key `8848` on an
SSD1306 OLED display.

## Hardware

- Raspberry Pi with GPIO header
- 5 × laser diode outputs (wired to GPIO output pins)
- 5 × photodiode/phototransistor receivers (wired to GPIO input pins with pull-ups)
- Piezo speaker/buzzer on a PWM-capable GPIO pin
- SSD1306 I²C OLED display

### Default pin map

| Note | Frequency (Hz) | Laser pin | Receiver pin |
| --- | --- | --- | --- |
| do | 261.63 | BCM 5 | BCM 12 |
| re | 293.66 | BCM 6 | BCM 16 |
| mi | 329.63 | BCM 13 | BCM 20 |
| fa | 349.23 | BCM 19 | BCM 21 |
| so | 392.00 | BCM 26 | BCM 18 |

The buzzer/speaker uses PWM on **BCM 17** by default. Adjust the pins in
`default_config()` inside `laser_harp.py` if your wiring differs.

## Software setup

Install required libraries on the Pi:

```
python3 -m venv ~/dianchuang-env
source ~/dianchuang-env/bin/activate
pip install luma.oled pillow
python laser-harp.py
```

## Running

1. Connection.
2. Run the controller:

   ```
   python3 laser_harp.py
   ```

3. Break a beam to play its note. When the melody `mi re do re mi mi mi` is
   completed, the OLED will display `Sequence found!` and `Key: 8848`.
