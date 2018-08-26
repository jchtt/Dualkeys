# Dualkeys: dual-role keys with evdev and uinput

## TODO: Explain algorithm

Essentially, save all sequences starting with a dual key and wait for the first key raise.
Then, interpret all previous dual keys as modifiers.

## Usage

Dualkeys needs access to your device files and to uinput.
The easiest way to get this is by running it as root, for example with sudo.

First, you might want to learn the scan codes of the keys you want to endow with dual roles.
For this, use
```
./Dualkeys.py -p
```
and press the keys in question.

Example output:
```
keycode = KEY_SPACE, scancode = 57, keystate = 1

keycode = KEY_SPACE, scancode = 57, keystate = 0
 
keycode = KEY_LEFTCTRL, scancode = 29, keystate = 1

keycode = KEY_LEFTCTRL, scancode = 29, keystate = 0
```

A dual-role key is specified by three scancodes: that of the actual key pressed on the keyboard, that of the key that is to be triggered when the key is pressed and released on its own, and the one that is triggered when it is pressed in conjunction with another key.
These three scancodes in this order are passed as arguments to the `-k` switch.
For example, to make the space bar double as control key, use
```
python Dualkeys.py -k 57 57 29
```

