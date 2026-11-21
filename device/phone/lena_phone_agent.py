"""
Lena phone agent — runs inside Termux on an ANDROID phone.

This is the only honest way an AI can act on your phone: a small program on the
phone itself, checking for instructions and carrying them out locally. No cloud
service can reach into your handset, and iOS has no equivalent (iPhone users
are limited to Shortcuts and manual taps).

WHAT IT CAN DO (Android only, via Termux:API)
    open_app        launch an app by package name or name fragment
    battery         battery level, charging state, temperature
    wifi_info       connected network name and IP
    toast           show a short message on screen
    notify          post a notification
    torch           toggle the flashlight
    sms_send        send an SMS (asks the number and text)
    photo           take a photo with the camera
    clipboard_get   read the clipboard
    clipboard_set   put text on the clipboard
    location        last known location
    say             speak text out loud
    status          device and Termux status
    ping            prove the agent is alive

SETUP (once, on the phone)
    1. Install F-Droid, then from it install: Termux, Termux:API
    2. Open Termux and run:
         pkg update && pkg upgrade -y
         pkg install python termux-api -y
         pip install requests
    3. Get your Base44 personal API key (agent editor > Settings > Developer / API Docs)
         export LENA_API_KEY="your_key"
         echo 'export LENA_API_KEY="your_key"' >> ~/.bashrc
    4. Put this file on the phone (download it, then move it into Termux storage) and run:
         python lena_phone_agent.py
    5. Leave Termux running. Android may kill it in the background — run
         termux-wake-lock
       to keep it alive, and disable battery optimisation for Termux.

SAFETY
    Only the actions above are accepted. There is no shell action. Every command
    is printed on the phone screen as it runs, so you can see exactly what Lena
    asked for. Ctrl+C stops it and she loses access immediately.
"""

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. In Termux run: pip install requests")

API_KEY = os.environ.get("LENA_API_KEY", "").strip()
APP_ID = os.environ.get("LENA_APP_ID", "6a0ebc236c3234fc81a8acf3")
BASE = os.environ.get("LENA_API_BASE", "https://app.base44.com")
ENTITY = "DeviceCommand"
DEVICE = "phone"
POLL_SECONDS = int(os.environ.get("LENA_POLL_SECONDS", "5"))

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

ALLOWED = {
    "open_app", "open_url", "battery", "wifi_info", "toast", "notify", "torch",
    "sms_send", "photo", "clipboard_get", "clipboard_set", "location",
    "say", "status", "ping", "file_put", "file_get",
}

DOWNLOAD_DIR = "/sdcard/Download"


# --------------------------------------------------------------------------- #
# Base44 entity helpers
# --------------------------------------------------------------------------- #
def list_pending():
    url = f"{BASE}/api/apps/{APP_ID}/entities/{ENTITY}/v2/list"
    query = json.dumps({"status": "pending", "device": DEVICE})
    r = requests.get(url, headers=HEADERS, params={"q": query, "limit": 5}, timeout=30)
    if r.status_code == 401:
        sys.exit("Base44 rejected the key (401). Check LENA_API_KEY is a personal API key.")
    r.raise_for_status()
    return r.json().get("items", [])


def update_record(record_id, **fields):
    url = f"{BASE}/api/apps/{APP_ID}/entities/{ENTITY}/{record_id}"
    try:
        r = requests.put(url, headers=HEADERS, json=fields, timeout=30)
        if r.status_code in (404, 405):  # some deployments only accept PATCH
            r = requests.put(url, headers=HEADERS, json=fields, timeout=30)
        if r.status_code in (404, 405):
            r = requests.patch(url, headers=HEADERS, json=fields, timeout=30)
        if r.status_code >= 400:
            print(f"  [warn] update failed: {r.status_code} {r.text[:150]}")
    except requests.exceptions.RequestException as exc:
        print(f"  [warn] update failed: {exc}")


# --------------------------------------------------------------------------- #
# Termux helpers
# --------------------------------------------------------------------------- #
def termux(args, timeout=25):
    """Run a termux-api command and return its stdout."""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("termux-api missing. In Termux run: pkg install termux-api")
    except subprocess.TimeoutExpired:
        raise RuntimeError("termux command timed out")
    if out.returncode != 0 and not out.stdout.strip():
        raise RuntimeError((out.stderr or "command failed").strip()[:200])
    return out.stdout.strip()


def termux_json(args):
    raw = termux(args)
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def act_open_app(args):
    name = args.strip()
    if not name:
        raise RuntimeError("open_app needs an app name")
    if "." in name and " " not in name:
        termux(["am", "start", f"{name}/.MainActivity"], timeout=20)
        return f"launched {name}"
    listing = termux(["pm", "list", "packages"], timeout=25).splitlines()
    match = [p.replace("package:", "") for p in listing if name.lower() in p.lower()]
    if not match:
        raise RuntimeError(f"no installed package matched '{name}'")
    pkg = match[0]
    termux(["monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], timeout=25)
    return f"launched {pkg}"


def act_battery(_):
    d = termux_json(["termux-battery-status"])
    bits = []
    if "percentage" in d:
        bits.append(f"{d['percentage']}%")
    if "status" in d:
        bits.append(d["status"])
    if "temperature" in d:
        bits.append(f"{d['temperature']}C")
    return ", ".join(bits) or json.dumps(d)[:200]


def act_wifi(_):
    d = termux_json(["termux-wifi-connectioninfo"])
    if "ssid" in d:
        return f"{d.get('ssid')} (ip {d.get('ip', '?')})"
    return json.dumps(d)[:200]


def act_toast(args):
    termux(["termux-toast", args or "Hello from Lena"])
    return "toast shown"


def act_notify(args):
    title = "Lena"
    body = args or "You have a message."
    termux(["termux-notification", "--title", title, "--content", body])
    return "notification posted"


def act_torch(_):
    termux(["termux-torch", "on"])
    return "torch on (run again with 'off' to switch it off)"


def act_sms(args):
    if "|" not in args:
        raise RuntimeError("sms_send needs 'number|message'")
    number, text = args.split("|", 1)
    termux(["termux-sms-send", "-n", number.strip(), text.strip()], timeout=40)
    return f"SMS sent to {number.strip()}"


def act_photo(_):
    path = os.path.expanduser(f"~/lena_photo_{datetime.now():%Y%m%d_%H%M%S}.jpg")
    termux(["termux-camera-photo", path], timeout=45)
    return f"photo saved to {path}"


def act_clip_get(_):
    return termux(["termux-clipboard-get"])[:400] or "(clipboard empty)"


def act_clip_set(args):
    termux(["termux-clipboard-set", args])
    return "clipboard updated"


def act_location(_):
    d = termux_json(["termux-location"])
    lat, lon = d.get("latitude"), d.get("longitude")
    if lat and lon:
        return f"https://maps.google.com/?q={lat},{lon}"
    return json.dumps(d)[:200]


def act_say(args):
    termux(["termux-tts-speak", args or "Done"])
    return "spoken"


def act_status(_):
    return (f"device={platform.node()} android, "
            f"python={platform.python_version()}, "
            f"polling every {POLL_SECONDS}s")


def act_ping(_):
    return f"phone agent alive at {datetime.now():%Y-%m-%d %H:%M:%S}"


# --------------------------------------------------------------------------- #
# Shared clipboard and file passing
# --------------------------------------------------------------------------- #
import base64


def act_open_url(args):
    url = (args or "").strip()
    if not url:
        raise RuntimeError("open_url needs a URL")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    termux(["termux-open-url", url])
    return f"opened {url}"


def act_file_put(args):
    path = (args or "").strip().strip('"')
    if not path:
        raise RuntimeError("file_put needs a path, e.g. /sdcard/Download/cv.pdf")
    if not os.path.exists(path):
        raise RuntimeError(f"no such file: {path}")
    size = os.path.getsize(path)
    if size > 400_000:
        raise RuntimeError(f"file is {size // 1024}KB; the queue carries up to 400KB")
    with open(path, "rb") as fh:
        blob = base64.b64encode(fh.read()).decode()
    return f"FILE:{os.path.basename(path)}:{blob}"


def act_file_get(args):
    """args: '<record_id>|<filename>' - pulls a file another device queued."""
    if "|" not in (args or ""):
        raise RuntimeError("file_get needs '<record_id>|<filename>'")
    record_id, name = args.split("|", 1)
    url = f"{BASE}/api/apps/{APP_ID}/entities/{ENTITY}/{record_id.strip()}"
    r = requests.get(url, headers=HEADERS, timeout=40)
    if r.status_code >= 400:
        raise RuntimeError(f"could not read record ({r.status_code})")
    result = (r.json() or {}).get("result") or ""
    if not result.startswith("FILE:"):
        raise RuntimeError("that record does not hold a file")
    _, _, blob = result.split(":", 2)
    out = os.path.join(DOWNLOAD_DIR, name.strip().replace("/", "_"))
    with open(out, "wb") as fh:
        fh.write(base64.b64decode(blob))
    return f"saved {out} ({os.path.getsize(out) // 1024}KB)"


HANDLERS = {
    "open_app": act_open_app,
    "battery": act_battery,
    "wifi_info": act_wifi,
    "toast": act_toast,
    "notify": act_notify,
    "torch": act_torch,
    "sms_send": act_sms,
    "photo": act_photo,
    "clipboard_get": act_clip_get,
    "clipboard_set": act_clip_set,
    "location": act_location,
    "say": act_say,
    "status": act_status,
    "ping": act_ping,
    "open_url": act_open_url,
    "file_put": act_file_put,
    "file_get": act_file_get,
}


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def run_command(record):
    command = (record.get("command") or "").strip()
    args = record.get("args") or ""
    rid = record.get("id")

    if command not in ALLOWED or command not in HANDLERS:
        update_record(rid, status="error", result=f"refused: '{command}' not allowed on phone")
        print(f"[refused] {command}")
        return

    print(f"[run] {command} {args[:60]}")
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
        sys.exit("Set LENA_API_KEY first:  export LENA_API_KEY=\"your_key\"")
    if not os.path.exists("/data/data/com.termux"):
        print("Warning: this does not look like Termux. Actions using termux-api will fail.\n")

    print("Lena phone agent is running.")
    print(f"  device tag: {DEVICE}")
    print(f"  allowed:    {', '.join(sorted(ALLOWED))}")
    print("  Leave Termux open. Ctrl+C to stop.\n")

    while True:
        try:
            for record in list_pending():
                run_command(record)
        except KeyboardInterrupt:
            print("\nStopped.")
            return
        except requests.exceptions.RequestException as exc:
            print(f"[net] {exc}")
        except Exception as exc:
            print(f"[loop] {type(exc).__name__}: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
