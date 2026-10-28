#!/usr/bin/env python3
"""What Got Plugged? - a small GUI that tells you which USB device you just plugged in.

It polls the system every second or so, compares the list of USB devices (and the
entries in /dev and mounted volumes) against a baseline, and pauses as soon as
something new shows up, displaying its name and details.

Works on macOS (via `ioreg`) and Linux (via /sys/bus/usb/devices).
Needs only the Python standard library (tkinter included).

For USB audio devices (which often all call themselves "USB AUDIO CODEC") it can
add a renamed copy of the device:
  - macOS: an aggregate device, like Audio MIDI Setup makes (stays until removed).
  - Linux: a PulseAudio/PipeWire remap sink + source via `pactl` (gone after the
    sound server restarts or the machine reboots).

"""

import ctypes
import glob
import os
import platform
import plistlib
import re
import struct
import subprocess
import time
import tkinter as tk
from tkinter import ttk

SYSTEM = platform.system()  # "Darwin" or "Linux"

# After a new USB device appears, keep watching this long for its /dev nodes and
# mounts to show up (disks and serial ports often appear a moment later).
SETTLE_SECONDS = 3.0


# ---------------------------------------------------------------------------
# Snapshots: each returns {key: info_dict}
# ---------------------------------------------------------------------------

def _clean(s):
    return s.strip() if isinstance(s, str) else s


MAC_SPEEDS = {0: "1.5 Mb/s (Low)", 1: "12 Mb/s (Full)", 2: "480 Mb/s (High)",
              3: "5 Gb/s (SuperSpeed)", 4: "10 Gb/s (SuperSpeed+)"}


def usb_devices_macos():
    out = subprocess.run(
        ["ioreg", "-p", "IOUSB", "-l", "-w0", "-a"],
        capture_output=True, check=True, timeout=10,
    ).stdout
    root = plistlib.loads(out)
    devices = {}

    def walk(node):
        if "idVendor" in node and "idProduct" in node:
            name = _clean(node.get("USB Product Name") or node.get("kUSBProductString"))
            # Skip the controllers' built-in "root hub" entries.
            if not (name and "Root Hub Simulation" in name):
                info = {
                    "name": name,
                    "vendor": _clean(node.get("USB Vendor Name") or node.get("kUSBVendorString")),
                    "vid": node["idVendor"],
                    "pid": node["idProduct"],
                    "serial": _clean(node.get("USB Serial Number") or node.get("kUSBSerialNumberString")),
                    "location": f"0x{node.get('locationID', 0):08x}",
                    "speed": MAC_SPEEDS.get(node.get("Device Speed")),
                }
                key = (info["location"], info["vid"], info["pid"], info["serial"])
                devices[key] = info
        for child in node.get("IORegistryEntryChildren", []):
            walk(child)

    walk(root)
    return devices


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def usb_devices_linux(base="/sys/bus/usb/devices"):
    devices = {}
    for entry in os.listdir(base):
        # Entries with ':' are interfaces, not devices.
        if ":" in entry:
            continue
        path = os.path.join(base, entry)
        vid = _read(os.path.join(path, "idVendor"))
        pid = _read(os.path.join(path, "idProduct"))
        if not vid or not pid:
            continue
        if entry.startswith("usb"):  # root hubs
            continue
        info = {
            "name": _read(os.path.join(path, "product")),
            "vendor": _read(os.path.join(path, "manufacturer")),
            "vid": int(vid, 16),
            "pid": int(pid, 16),
            "serial": _read(os.path.join(path, "serial")),
            "location": entry,
            "speed": (_read(os.path.join(path, "speed")) or "") + " Mb/s",
        }
        busnum = _read(os.path.join(path, "busnum"))
        devnum = _read(os.path.join(path, "devnum"))
        key = (entry, busnum, devnum)
        devices[key] = info
    return devices


def usb_devices():
    if SYSTEM == "Darwin":
        return usb_devices_macos()
    if SYSTEM == "Linux":
        return usb_devices_linux()
    raise RuntimeError(f"Unsupported platform: {SYSTEM}")


def dev_nodes():
    """Names in /dev (plus a few useful Linux subfolders)."""
    names = set()
    try:
        names.update(os.listdir("/dev"))
    except OSError:
        pass
    if SYSTEM == "Linux":
        for sub in ("serial/by-id", "disk/by-id", "input/by-id"):
            try:
                names.update(f"{sub}/{n}" for n in os.listdir(f"/dev/{sub}"))
            except OSError:
                pass
    return names


def mounted_volumes():
    if SYSTEM == "Darwin":
        try:
            return {f"/Volumes/{n}" for n in os.listdir("/Volumes")}
        except OSError:
            return set()
    mounts = set()
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].startswith("/dev/"):
                    mounts.add(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return mounts


def snapshot():
    return {"usb": usb_devices(), "dev": dev_nodes(), "mounts": mounted_volumes()}


def display_name(info):
    if info.get("name"):
        return info["name"]
    return f"Unnamed USB device ({info['vid']:04x}:{info['pid']:04x})"


# ---------------------------------------------------------------------------
# Core Audio (macOS only): give a USB audio device a recognizable name by
# wrapping it in an aggregate device, like Audio MIDI Setup does.
# ---------------------------------------------------------------------------

# Our aggregate devices get UIDs starting with this, so we never touch others.
OUR_UID_PREFIX = "com.whatgotplugged."


class CoreAudio:
    """Tiny ctypes wrapper around the few Core Audio calls we need."""

    UTF8 = 0x08000100
    SYSTEM_OBJECT = 1

    class _Addr(ctypes.Structure):
        _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                    ("element", ctypes.c_uint32)]

    def __init__(self):
        ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        vp, u32 = ctypes.c_void_p, ctypes.c_uint32
        ca.AudioObjectGetPropertyDataSize.argtypes = [u32, vp, u32, vp, vp]
        ca.AudioObjectGetPropertyData.argtypes = [u32, vp, u32, vp, vp, vp]
        ca.AudioHardwareCreateAggregateDevice.argtypes = [vp, vp]
        ca.AudioHardwareDestroyAggregateDevice.argtypes = [u32]
        cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, u32]
        cf.CFRelease.argtypes = [vp]
        cf.CFDataCreate.argtypes = [vp, ctypes.c_char_p, ctypes.c_long]
        cf.CFDataCreate.restype = vp
        cf.CFPropertyListCreateWithData.argtypes = [vp, vp, ctypes.c_ulong, vp, vp]
        cf.CFPropertyListCreateWithData.restype = vp
        self.ca, self.cf = ca, cf

    @staticmethod
    def _code(s):
        return struct.unpack(">I", s.encode())[0]

    def _get(self, obj, selector, buf):
        addr = self._Addr(self._code(selector), self._code("glob"), 0)
        size = ctypes.c_uint32(ctypes.sizeof(buf))
        return self.ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None,
                                                  ctypes.byref(size), ctypes.byref(buf))

    def _get_string(self, obj, selector):
        ref = ctypes.c_void_p()
        if self._get(obj, selector, ref) or not ref.value:
            return None
        buf = ctypes.create_string_buffer(1024)
        self.cf.CFStringGetCString(ref, buf, len(buf), self.UTF8)
        self.cf.CFRelease(ref)
        return buf.value.decode("utf-8", "replace")

    def devices(self):
        """All audio devices: [{"id", "name", "uid", "transport"}]."""
        addr = self._Addr(self._code("dev#"), self._code("glob"), 0)
        size = ctypes.c_uint32()
        if self.ca.AudioObjectGetPropertyDataSize(self.SYSTEM_OBJECT, ctypes.byref(addr),
                                                  0, None, ctypes.byref(size)):
            return []
        ids = (ctypes.c_uint32 * (size.value // 4))()
        if self._get(self.SYSTEM_OBJECT, "dev#", ids):
            return []
        result = []
        for dev_id in ids:
            transport = ctypes.c_uint32()
            self._get(dev_id, "tran", transport)
            result.append({
                "id": dev_id,
                "name": self._get_string(dev_id, "lnam"),
                "uid": self._get_string(dev_id, "uid ") or "",
                "transport": struct.pack(">I", transport.value).decode("latin-1"),
            })
        return result

    def create_aggregate(self, name, uid, sub_uids):
        """Create a (non-private, so other apps see it) aggregate device."""
        desc = {
            "name": name,
            "uid": uid,
            "subdevices": [{"uid": u} for u in sub_uids],
            "master": sub_uids[0],
            "private": 0,
            "stacked": 0,
        }
        data = plistlib.dumps(desc, fmt=plistlib.FMT_BINARY)
        cf_data = self.cf.CFDataCreate(None, data, len(data))
        cf_dict = self.cf.CFPropertyListCreateWithData(None, cf_data, 0, None, None)
        self.cf.CFRelease(cf_data)
        if not cf_dict:
            raise RuntimeError("couldn't build the device description")
        new_id = ctypes.c_uint32()
        status = self.ca.AudioHardwareCreateAggregateDevice(cf_dict, ctypes.byref(new_id))
        self.cf.CFRelease(cf_dict)
        if status:
            raise RuntimeError(f"Core Audio error {status}")
        return new_id.value

    def destroy(self, dev_id):
        status = self.ca.AudioHardwareDestroyAggregateDevice(dev_id)
        if status:
            raise RuntimeError(f"Core Audio error {status}")


_core_audio = None


def core_audio():
    global _core_audio
    if _core_audio is None:
        _core_audio = CoreAudio()
    return _core_audio


def audio_key(usb_info):
    """Identify a USB device the way Core Audio does: serial, else location in hex."""
    return usb_info.get("serial") or format(int(usb_info["location"], 16), "x")


def _macos_audio_devices_for(usb_info):
    """Core Audio devices belonging to this USB device.

    USB audio UIDs look like
    'AppleUSBAudioEngine:<vendor>:<product>:<serial or location>:<interface>',
    usually one for output (…:1) and one for input (…:2).
    """
    key = audio_key(usb_info)
    matches = []
    for d in core_audio().devices():
        parts = d["uid"].rsplit(":", 2)
        if d["transport"] == "usb " and len(parts) == 3 and parts[1] == key:
            matches.append(d)
    return sorted(matches, key=lambda d: d["uid"])


def our_aggregate_uid(usb_info):
    return OUR_UID_PREFIX + audio_key(usb_info)


def _macos_find_name(usb_info):
    uid = our_aggregate_uid(usb_info)
    return next((d for d in core_audio().devices() if d["uid"] == uid), None)


def _macos_name(usb_info, name):
    """Create (or replace) a named aggregate wrapping this USB device's audio."""
    subs = _macos_audio_devices_for(usb_info)
    if not subs:
        raise RuntimeError("no Core Audio device found for this USB device")
    existing = _macos_find_name(usb_info)
    if existing:
        core_audio().destroy(existing["id"])
    core_audio().create_aggregate(name, our_aggregate_uid(usb_info),
                                  [d["uid"] for d in subs])


def _macos_remove_name(usb_info):
    existing = _macos_find_name(usb_info)
    if existing:
        core_audio().destroy(existing["id"])
    return existing


# ---------------------------------------------------------------------------
# PulseAudio / PipeWire (Linux, via `pactl`): give a USB audio device a
# recognizable name by adding a renamed "remap" sink/source on top of it.
# These last until the sound server restarts (e.g. a reboot).
# ---------------------------------------------------------------------------

LINUX_PREFIX = "wgp_"


def _pactl(*args):
    try:
        r = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=10,
                           env=dict(os.environ, LC_ALL="C"))
    except FileNotFoundError:
        raise RuntimeError("pactl not found (needs PulseAudio or PipeWire with pipewire-pulse)")
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"pactl {args[0]} failed")
    return r.stdout


def _parse_pactl_list(text):
    """Parse `pactl list sinks|sources` into [{"name", "description", "props"}]."""
    items, cur, in_props = [], None, False
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():              # "Sink #12" starts a new entry
            cur = {"name": None, "description": None, "props": {}}
            items.append(cur)
            in_props = False
            continue
        if cur is None:
            continue
        s = line.strip()
        if line.startswith("\t\t") or line.startswith("        "):   # nested line
            if in_props and " = " in s:
                k, v = s.split(" = ", 1)
                cur["props"][k] = v.strip('"')
            continue
        in_props = s == "Properties:"
        if s.startswith("Name: "):
            cur["name"] = s[len("Name: "):]
        elif s.startswith("Description: "):
            cur["description"] = s[len("Description: "):]
    return items


def _linux_key(usb_info):
    return re.sub(r"[^A-Za-z0-9]+", "_", usb_info.get("serial") or usb_info["location"])


def _is_ours(name, usb_info):
    return bool(re.fullmatch(rf"{LINUX_PREFIX}{re.escape(_linux_key(usb_info))}_(out|in)\d*",
                             name or ""))


def _linux_cards(usb_info, base="/sys/bus/usb/devices"):
    """ALSA card numbers that belong to this USB device."""
    entry = usb_info["location"]
    cards = set()
    for p in glob.glob(os.path.join(base, entry, entry + ":*", "sound", "card*")):
        num = os.path.basename(p)[len("card"):]
        if num.isdigit():
            cards.add(num)
    return cards


def _linux_list(kind):
    return _parse_pactl_list(_pactl("list", kind + "s"))


def _linux_audio_devices_for(usb_info):
    """Sound-server outputs (sinks) and inputs (sources) of this USB device."""
    cards = _linux_cards(usb_info)
    if not cards:
        return []          # not an audio device; don't bother the sound server
    entry = usb_info["location"]
    found = []
    for kind in ("sink", "source"):
        for d in _linux_list(kind):
            name, p = d["name"] or "", d["props"]
            if (name.startswith(LINUX_PREFIX) or name.endswith(".monitor")
                    or p.get("device.class") == "monitor"):
                continue
            card = p.get("alsa.card") or p.get("api.alsa.card") or p.get("api.alsa.pcm.card")
            if card in cards or f"/{entry}/{entry}:" in p.get("sysfs.path", ""):
                found.append({"name": d["description"] or name, "uid": name, "kind": kind})
    return found


def _linux_find_name(usb_info):
    for kind in ("sink", "source"):
        for d in _linux_list(kind):
            if _is_ours(d["name"], usb_info):
                return {"name": d["description"] or d["name"]}
    return None


def _linux_remove_name(usb_info):
    existing = _linux_find_name(usb_info)
    for line in _pactl("list", "short", "modules").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or parts[1] not in ("module-remap-sink", "module-remap-source"):
            continue
        m = re.search(r"\b(?:sink|source)_name=(\S+)", parts[2])
        if m and _is_ours(m.group(1).strip("\"'"), usb_info):
            _pactl("unload-module", parts[0])
    return existing


def _linux_name(usb_info, name):
    subs = _linux_audio_devices_for(usb_info)
    if not subs:
        raise RuntimeError("no sound-server device found for this USB device")
    _linux_remove_name(usb_info)
    desc = re.sub(r"[\"'\\]", "", name)          # quotes would break the module arguments
    key = _linux_key(usb_info)
    counts = {"sink": 0, "source": 0}
    for d in subs:
        kind = d["kind"]
        counts[kind] += 1
        suffix = ("out" if kind == "sink" else "in") + (str(counts[kind]) if counts[kind] > 1 else "")
        _pactl("load-module", f"module-remap-{kind}",
               f"{kind}_name={LINUX_PREFIX}{key}_{suffix}",
               f"master={d['uid']}",
               f"{kind}_properties=\"device.description='{desc}'\"")


# ---------------------------------------------------------------------------
# Platform-independent entry points used by the GUI
# ---------------------------------------------------------------------------

def audio_devices_for(usb_info):
    if SYSTEM == "Darwin":
        return _macos_audio_devices_for(usb_info)
    if SYSTEM == "Linux":
        return _linux_audio_devices_for(usb_info)
    return []


def find_audio_name(usb_info):
    """The name we gave this device ({"name": ...}), or None."""
    return _macos_find_name(usb_info) if SYSTEM == "Darwin" else _linux_find_name(usb_info)


def name_audio_device(usb_info, name):
    """Create (or replace) a renamed audio device for this USB device."""
    if SYSTEM == "Darwin":
        _macos_name(usb_info, name)
    else:
        _linux_name(usb_info, name)


def remove_audio_name(usb_info):
    """Remove our renamed device; returns what was removed ({"name": ...}) or None."""
    return _macos_remove_name(usb_info) if SYSTEM == "Darwin" else _linux_remove_name(usb_info)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        root.title("What Got Plugged?")
        root.geometry("620x480")
        root.minsize(480, 360)

        self.state = "stopped"   # stopped | watching | settling | found
        self.baseline = None
        self.found = []          # USB devices found during this detection
        self.settle_until = 0
        self.after_id = None

        frm = ttk.Frame(root, padding=16)
        frm.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Press Start, then plug in a USB device.")
        ttk.Label(frm, textvariable=self.status_var, font=("TkDefaultFont", 12)).pack(anchor="w")

        self.name_var = tk.StringVar(value="—")
        ttk.Label(frm, textvariable=self.name_var, font=("TkDefaultFont", 22, "bold"),
                  wraplength=580).pack(anchor="w", pady=(12, 8))

        self.details = tk.Text(frm, height=12, wrap="word", font=("TkFixedFont", 11),
                               relief="flat", borderwidth=1)
        self.details.pack(fill="both", expand=True)
        self.details.configure(state="disabled")

        # Shown only when the detected device has audio devices (macOS / Linux).
        self.audio_target = None
        self.audio_row = ttk.Frame(frm)
        ttk.Label(self.audio_row, text="Name this audio device:").pack(side="left")
        self.audio_name_var = tk.StringVar()
        entry = ttk.Entry(self.audio_row, textvariable=self.audio_name_var, width=24)
        entry.pack(side="left", padx=6, fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.apply_audio_name())
        ttk.Button(self.audio_row, text="Apply name",
                   command=self.apply_audio_name).pack(side="left")
        self.remove_name_btn = ttk.Button(self.audio_row, text="Remove name",
                                          command=self.remove_audio_name)
        self.remove_name_btn.pack(side="left", padx=(6, 0))

        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(12, 0))
        self.bar = bar
        self.start_btn = ttk.Button(bar, text="Start", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        self.copy_btn = ttk.Button(bar, text="Copy details", command=self.copy, state="disabled")
        self.copy_btn.pack(side="left")

        ttk.Label(bar, text="Poll every (s):").pack(side="right", padx=(0, 4))
        self.interval_var = tk.DoubleVar(value=1.0)
        ttk.Spinbox(bar, from_=0.25, to=10, increment=0.25, width=5,
                    textvariable=self.interval_var).pack(side="right")

    # -- helpers ------------------------------------------------------------

    def interval_ms(self):
        try:
            return max(100, int(float(self.interval_var.get()) * 1000))
        except (tk.TclError, ValueError):
            return 1000

    def set_details(self, text):
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def schedule(self, ms):
        self.cancel()
        self.after_id = self.root.after(ms, self.tick)

    def cancel(self):
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None

    # -- actions ------------------------------------------------------------

    def start(self):
        try:
            self.baseline = snapshot()
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"Couldn't read devices: {e}")
            return
        self.found = []
        self.hide_audio_row()
        self.state = "watching"
        self.status_var.set(f"Watching… {len(self.baseline['usb'])} USB devices currently connected. "
                            "Plug something in.")
        self.name_var.set("Waiting for a device…")
        self.set_details("")
        self.start_btn.configure(state="disabled", text="Resume")
        self.stop_btn.configure(state="normal")
        self.copy_btn.configure(state="disabled")
        self.schedule(self.interval_ms())

    def stop(self):
        self.cancel()
        self.state = "stopped"
        self.status_var.set("Stopped.")
        self.start_btn.configure(state="normal", text="Start")
        self.stop_btn.configure(state="disabled")

    def copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.name_var.get() + "\n" + self.details.get("1.0", "end"))

    # -- polling ------------------------------------------------------------

    def tick(self):
        self.after_id = None
        try:
            now = snapshot()
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"Error reading devices: {e} (retrying)")
            self.schedule(self.interval_ms())
            return

        old_usb = self.baseline["usb"]
        added = [now["usb"][k] for k in now["usb"].keys() - old_usb.keys()]
        removed = [old_usb[k] for k in old_usb.keys() - now["usb"].keys()]

        if self.state == "watching":
            if removed and not added:
                # Something was unplugged: just quietly update the baseline.
                names = ", ".join(display_name(d) for d in removed)
                self.status_var.set(f"Watching… (unplugged: {names})")
                self.baseline = now
            elif added:
                self.found = added
                self.state = "settling"
                self.settle_until = time.monotonic() + SETTLE_SECONDS
                self.name_var.set(", ".join(display_name(d) for d in added))
                self.status_var.set("Device detected! Gathering details…")
            self.schedule(self.interval_ms() if self.state == "watching" else 500)
            return

        if self.state == "settling":
            # Keep waiting briefly; show_result() re-diffs so late arrivals are included.
            if time.monotonic() < self.settle_until:
                self.schedule(500)
                return
            self.show_result(now)

    def show_result(self, now):
        new_dev = sorted(now["dev"] - self.baseline["dev"])
        new_mounts = sorted(now["mounts"] - self.baseline["mounts"])
        # Recompute so the list reflects the final state.
        added = [now["usb"][k] for k in now["usb"].keys() - self.baseline["usb"].keys()]
        if added:
            self.found = added

        lines = []
        for d in self.found:
            lines.append(f"Name:      {display_name(d)}")
            if d.get("vendor"):
                lines.append(f"Vendor:    {d['vendor']}")
            lines.append(f"VID:PID:   {d['vid']:04x}:{d['pid']:04x}")
            if d.get("serial"):
                lines.append(f"Serial:    {d['serial']}")
            lines.append(f"Location:  {d['location']}")
            if d.get("speed") and d["speed"] != " Mb/s":
                lines.append(f"Speed:     {d['speed']}")
            lines.append("")
        if new_dev:
            lines.append("New /dev entries:")
            lines += [f"  /dev/{n}" for n in new_dev]
            lines.append("")
        if new_mounts:
            lines.append("New mounted volumes:")
            lines += [f"  {m}" for m in new_mounts]

        names = [display_name(d) for d in self.found] or ["(device disappeared)"]
        self.name_var.set(", ".join(names))
        self.base_details = "\n".join(lines).strip()
        self.set_details(self.base_details)
        self.state = "found"
        self.update_audio_row(retry=True)
        self.status_var.set("Paused — found a new device. Press Resume to watch again.")
        self.start_btn.configure(state="normal", text="Resume")
        self.stop_btn.configure(state="disabled")
        self.copy_btn.configure(state="normal")
        self.root.bell()
        self.root.lift()

    # -- audio naming ---------------------------------------------------------

    def hide_audio_row(self):
        self.audio_target = None
        self.audio_row.pack_forget()

    def update_audio_row(self, retry=False):
        """Show the naming row if a found device has Core Audio devices."""
        if SYSTEM not in ("Darwin", "Linux") or self.state != "found" or not self.found:
            return
        try:
            target, subs = None, []
            for d in self.found:
                subs = audio_devices_for(d)
                if subs:
                    target = d
                    break
            existing = find_audio_name(target) if target else None
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"Couldn't read audio devices: {e}")
            return

        if not target:
            # Audio devices can register a moment after the USB device; check once more.
            if retry:
                self.root.after(2000, self.update_audio_row)
            return

        self.audio_target = target
        lines = [self.base_details, "", "Audio devices:"]
        lines += [f"  {d['name']}  [{d['uid']}]" for d in subs]
        if existing:
            lines.append(f"Currently named: {existing['name']}")
        self.set_details("\n".join(lines).strip())
        self.audio_name_var.set(existing["name"] if existing else "")
        self.remove_name_btn.configure(state="normal" if existing else "disabled")
        self.audio_row.pack(fill="x", pady=(12, 0), before=self.bar)

    def apply_audio_name(self):
        name = self.audio_name_var.get().strip()
        if not self.audio_target or not name:
            return
        try:
            name_audio_device(self.audio_target, name)
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"Couldn't name the audio device: {e}")
            return
        self.status_var.set(f"Audio device is now available as “{name}”.")
        self.update_audio_row()

    def remove_audio_name(self):
        if not self.audio_target:
            return
        try:
            removed = remove_audio_name(self.audio_target)
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"Couldn't remove the name: {e}")
            return
        if removed:
            self.status_var.set(f"Removed “{removed['name']}”.")
        self.update_audio_row()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
