# What Got Plugged?

A small desktop app that tells you which USB device you just plugged in, and lets
you give USB audio devices a recognizable name.

Many cheap USB audio interfaces all call themselves **"USB AUDIO CODEC"**, so with
several connected you can't tell them apart. This app catches the one you just
plugged in and lets you name it (for example "Guitar DI") so it shows up under
that name in your audio apps.

Works on **macOS** and **Linux** (tested on a Raspberry Pi 5). It's a single Python
file and needs only the standard library.

## Running it

```bash
python3 what_got_plugged.py
```

On Debian / Raspberry Pi OS you may need:

```bash
sudo apt install python3-tk pulseaudio-utils
```

`python3-tk` is needed for the window. `pulseaudio-utils` provides `pactl`, which
is only needed for audio naming. Run the app as your normal user, not with `sudo`.

## Opening it without a terminal

**macOS:** double-click **What Got Plugged.app**. It finds a Python 3 that has
tkinter (python.org, Homebrew, pyenv, conda, or Apple's `/usr/bin/python3`) and
runs `what_got_plugged.py` from the same folder as the app. If that file isn't
there, it runs the copy inside the app instead. Errors are written to
`~/Library/Logs/WhatGotPlugged.log`. You can drag the app to the Dock.

**Linux:** run the installer once to add the app to your application menu:

```bash
./linux/install.sh              # menu entry
./linux/install.sh --desktop    # menu entry + Desktop icon
./linux/install.sh --uninstall  # remove them
```

The menu entry points at this folder, so if you move the folder, run the
installer again. Errors are written to `~/.local/state/what-got-plugged.log`.

## How to use it

1. Press **Start**. The app remembers what's connected right now.
2. Plug in a USB device.
3. The app pauses and shows the device's name, vendor, VID:PID, serial number, USB
   location, speed, and any new `/dev` entries or mounted drives.
4. For audio devices, type a name in **Name this audio device** and press
   **Apply name** (or Enter).
5. Press **Resume** to watch for the next device.

**Copy details** puts the details on the clipboard. **Remove name** deletes the
renamed device. Naming the same device again replaces its old name.

## How audio naming works

The original "USB AUDIO CODEC" entry can't be renamed, so the app adds a renamed
copy of it alongside the original:

| | macOS | Linux |
|---|---|---|
| What it creates | An aggregate device with the codec's input and output, the same as Audio MIDI Setup makes | A PulseAudio/PipeWire remap output and remap input (`module-remap-sink` / `module-remap-source`) |
| How long it lasts | Until you remove it, here or in Audio MIDI Setup | Until the sound server restarts or the computer reboots |

**Names are tied to the USB port.** These codecs usually have no serial number,
so the only way to tell two of them apart is where they're plugged in. If you
move a codec to a different port, name it again. Devices that do report a serial
number keep their name on any port.

The app only changes renamed devices it created itself. Anything you made in
Audio MIDI Setup is left alone.

## License

MIT. See [LICENSE](LICENSE).
