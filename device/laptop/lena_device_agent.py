"""
Lena device agent (laptop) - standard library only.

No pip. No requests. Nothing to install. If Python runs on the machine, this
runs. It polls a Base44 entity for commands, carries them out locally, and
writes the result back.

RUN
    Windows:      py lena_device_agent.py
    macOS/Linux:  python3 lena_device_agent.py

BEFORE THE FIRST RUN, set your key:
    Windows:      setx LENA_API_KEY "your_key"      then close and reopen the terminal
    macOS/Linux:  export LENA_API_KEY="your_key"

WHAT IT CAN DO (the entire list)
    open_url      open a web address in the default browser
    open_app      launch an application
    screenshot    save a screenshot to the Desktop (Windows/macOS, or Linux with scrot)
    lock_screen   lock the machine
    volume_up     raise the volume
    volume_down   lower the volume
    mute          toggle mute
    say           speak text out loud using the system voice
    status        report OS, machine name and time
    ping          prove the agent is alive

No shell. No arbitrary command. Ctrl+C stops it and access ends immediately.
"""

import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

API_KEY = os.environ.get("LENA_API_KEY", "").strip()
APP_ID = os.environ.get("LENA_APP_ID", "6a0ebc236c3234fc81a8acf3")
BASE = os.environ.get("LENA_API_BASE", "https://app.base44.com").rstrip("/")
ENTITY = "DeviceCommand"
DEVICE = os.environ.get("LENA_DEVICE", "laptop")
POLL_SECONDS = int(os.environ.get("LENA_POLL_SECONDS", "4"))

ALLOWED = {"open_url", "open_app", "screenshot", "lock_screen",
           "volume_up", "volume_down", "mute", "say", "status", "ping",
           "clipboard_get", "clipboard_set", "file_put", "file_get"}

SYSTEM = platform.system()  # Windows | Darwin | Linux


# --------------------------------------------------------------------------- #
# HTTP with the standard library only
# --------------------------------------------------------------------------- #
def http(method, url, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, {"error": body[:400]}
    except urllib.error.URLError as exc:
        return 0, {"error": f"network: {exc.reason}"}


def list_pending():
    query = urllib.parse.quote(json.dumps({"status": "pending", "device": DEVICE}))
    url = f"{BASE}/api/apps/{APP_ID}/entities/{ENTITY}/v2/list?q={query}&limit=5"
    status, data = http("GET", url)
    if status == 401:
        sys.exit("Base44 rejected the key (401). Check LENA_API_KEY is a personal API key.")
    if status == 0:
        print(f"  [net] {data.get('error')}")
        return []
    if status >= 400:
        print(f"  [http {status}] {str(data)[:200]}")
        return []
    return data.get("items", [])


def update_record(record_id, **fields):
    url = f"{BASE}/api/apps/{APP_ID}/entities/{ENTITY}/{record_id}"
    status, data = http("PUT", url, fields)
    if status >= 400:
        # some deployments only accept PATCH
        status, data = http("PATCH", url, fields)
    if status >= 400:
        print(f"  [warn] could not save result: {status} {str(data)[:160]}")


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def desktop_dir():
    d = Path.home() / "Desktop"
    return d if d.exists() else Path.home()


def act_open_url(args):
    url = (args or "").strip()
    if not url:
        raise ValueError("open_url needs a URL")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"opened {url}"


def act_open_app(args):
    name = (args or "").strip()
    if not name:
        raise ValueError("open_app needs an application name or path")
    if SYSTEM == "Windows":
        subprocess.Popen(f'start "" "{name}"', shell=True)
    elif SYSTEM == "Darwin":
        subprocess.Popen(["open", "-a", name])
    else:
        subprocess.Popen([name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"launched {name}"


def act_screenshot(_):
    path = desktop_dir() / f"lena_{datetime.now():%Y%m%d_%H%M%S}.png"
    if SYSTEM == "Windows":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
            f"$bmp.Save('{path}');"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=40)
    elif SYSTEM == "Darwin":
        subprocess.run(["screencapture", "-x", str(path)], check=True, timeout=40)
    else:
        subprocess.run(["scrot", str(path)], check=True, timeout=40)
    return f"saved {path}"


def act_lock(_):
    if SYSTEM == "Windows":
        subprocess.Popen("rundll32.exe user32.dll,LockWorkStation", shell=True)
    elif SYSTEM == "Darwin":
        subprocess.Popen(["pmset", "displaysleepnow"])
    else:
        subprocess.Popen(["loginctl", "lock-session"])
    return "screen locked"


def _volume_key(key):
    if SYSTEM == "Windows":
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"(New-Object -ComObject WScript.Shell).SendKeys([char]174)"],
                       check=False, timeout=15)
        return
    if SYSTEM == "Darwin":
        cmd = {"up": "set volume output volume ((output volume of (get volume settings)) + 10)",
               "down": "set volume output volume ((output volume of (get volume settings)) - 10)",
               "mute": "set volume output muted not (output volume muted of (get volume settings))"}[key]
        subprocess.run(["osascript", "-e", cmd], check=False, timeout=15)
        return
    for cmd in (["amixer", "-q", "sset", "Master", "5%+" if key == "up" else "5%-"],
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+5%" if key == "up" else "-5%"]):
        try:
            subprocess.run(cmd, check=False, timeout=15)
            return
        except FileNotFoundError:
            continue


def act_volume_up(_):
    _volume_key("up")
    return "volume up"


def act_volume_down(_):
    _volume_key("down")
    return "volume down"


def act_mute(_):
    _volume_key("mute")
    return "mute toggled"


def act_say(args):
    text = (args or "").strip() or "Done."
    if SYSTEM == "Windows":
        safe = text.replace("'", "''")
        script = ("Add-Type -AssemblyName System.Speech;"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"$s.Speak('{safe}')")
        subprocess.Popen(["powershell", "-NoProfile", "-Command", script])
    elif SYSTEM == "Darwin":
        subprocess.Popen(["say", text])
    else:
        for cmd in (["spd-say", text], ["espeak", text]):
            try:
                subprocess.Popen(cmd)
                break
            except FileNotFoundError:
                continue
    return "spoken"


def act_status(_):
    return (f"host={platform.node()}; os={SYSTEM} {platform.release()}; "
            f"python={platform.python_version()}; local_time={datetime.now():%Y-%m-%d %H:%M}")


def act_ping(_):
    return f"laptop agent alive at {datetime.now():%Y-%m-%d %H:%M:%S}"



# --------------------------------------------------------------------------- #
# Shared clipboard and file passing (this is what makes two devices feel like one)
# --------------------------------------------------------------------------- #
import base64


def _clipboard_read():
    if SYSTEM == "Windows":
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                             capture_output=True, text=True, timeout=20)
        return out.stdout.rstrip("\r\n")
    if SYSTEM == "Darwin":
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=20).stdout
    for cmd in (["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
        except FileNotFoundError:
            continue
    raise RuntimeError("no clipboard tool found (install xclip on Linux)")


def _clipboard_write(text):
    if SYSTEM == "Windows":
        subprocess.run(["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
                       input=text, text=True, timeout=20)
        return
    if SYSTEM == "Darwin":
        subprocess.run(["pbcopy"], input=text, text=True, timeout=20)
        return
    for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        try:
            subprocess.run(cmd, input=text, text=True, timeout=20)
            return
        except FileNotFoundError:
            continue
    raise RuntimeError("no clipboard tool found (install xclip on Linux)")


def act_clipboard_get(_):
    text = _clipboard_read()
    if not text.strip():
        return "clipboard is empty"
    return "CLIP:" + text[:4000]


def act_clipboard_set(args):
    _clipboard_write(args or "")
    return f"clipboard set ({len(args or '')} chars)"


def act_file_put(args):
    path = Path((args or "").strip().strip('"'))
    if not path.exists():
        raise ValueError(f"no such file: {path}")
    if path.stat().st_size > 400_000:
        raise ValueError(f"file is {path.stat().st_size // 1024}KB; the queue carries up to 400KB")
    blob = base64.b64encode(path.read_bytes()).decode()
    return f"FILE:{path.name}:{blob}"


def act_file_get(args):
    """args: '<record_id>|<filename>' — pulls a file another device put in the queue."""
    if "|" not in (args or ""):
        raise ValueError("file_get needs '<record_id>|<filename>'")
    record_id, name = args.split("|", 1)
    url = f"{BASE}/api/apps/{APP_ID}/entities/{ENTITY}/{record_id.strip()}"
    status, data = http("GET", url)
    if status >= 400:
        raise RuntimeError(f"could not read record ({status})")
    result = data.get("result") or ""
    if not result.startswith("FILE:"):
        raise RuntimeError("that record does not hold a file")
    _, _, blob = result.split(":", 2)
    out = desktop_dir() / name.strip().replace("/", "_").replace("\\", "_")
    out.write_bytes(base64.b64decode(blob))
    return f"saved {out} ({out.stat().st_size // 1024}KB)"


HANDLERS = {
    "open_url": act_open_url,
    "open_app": act_open_app,
    "screenshot": act_screenshot,
    "lock_screen": act_lock,
    "volume_up": act_volume_up,
    "volume_down": act_volume_down,
    "mute": act_mute,
    "say": act_say,
    "status": act_status,
    "ping": act_ping,
    "clipboard_get": act_clipboard_get,
    "clipboard_set": act_clipboard_set,
    "file_put": act_file_put,
    "file_get": act_file_get,
}


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #
def run_command(record):
    command = (record.get("command") or "").strip()
    args = record.get("args") or ""
    rid = record.get("id")

    if command not in ALLOWED or command not in HANDLERS:
        update_record(rid, status="error", result=f"refused: '{command}' not allowed on laptop")
        print(f"[refused] {command}")
        return

    print(f"[run] {command} {str(args)[:60]}")
    update_record(rid, status="running")
    try:
        result = HANDLERS[command](args)
        update_record(rid, status="done", result=str(result)[:900])
        print(f"  ok: {result}")
    except Exception as exc:
        update_record(rid, status="error", result=f"{type(exc).__name__}: {exc}"[:900])
        print(f"  failed: {exc}")


def main():
    if not API_KEY:
        print("Set your key first, then run this again.\n")
        if SYSTEM == "Windows":
            print('  setx LENA_API_KEY "your_key"     (then close and reopen this window)')
        else:
            print('  export LENA_API_KEY="your_key"')
        sys.exit(1)

    print("Lena laptop agent is running. No installs needed.")
    print(f"  device tag: {DEVICE}")
    print(f"  polling:    every {POLL_SECONDS}s")
    print(f"  actions:    {', '.join(sorted(ALLOWED))}")
    print("  Leave this window open. Ctrl+C stops it.\n")

    while True:
        try:
            for record in list_pending():
                run_command(record)
        except KeyboardInterrupt:
            print("\nStopped. Lena can no longer reach this machine.")
            return
        except Exception as exc:
            print(f"[loop] {type(exc).__name__}: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
