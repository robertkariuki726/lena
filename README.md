# Lena

A personal AI assistant built by Robert Kariuki — one identity across WhatsApp,
the browser and the devices in his own home. Named in September 2026.

## What is in here

| Path | What it is |
| --- | --- |
| `lena-v2.html` | The unified assistant: chat, tasks, browser tabs, voice in, spoken replies, voice picker, local storage, export. Connects to a Base44 Superagent over its API. |
| `lena-browser-v2.html` | Voice-only companion page: wake word ("Hey Lena"), barge-in, local commands (time, timers), voice picker. |
| `device/laptop/lena_device_agent.py` | Laptop agent. Standard library only, no pip. Polls a command queue and performs a fixed whitelist of actions. |
| `device/laptop/lena-device-agent.ps1` | The same agent for Windows in PowerShell, so nothing has to be installed. |
| `device/phone/lena_phone_agent.py` | Android agent for Termux + Termux:API. Battery, wifi, torch, notifications, SMS, photo, clipboard, location, opening apps. |
| `tools/mpesa_analyser.py` | Reads an M-Pesa statement, categorises every transaction and reports where the money went. Offline, no PIN, no upload. |
| `tools/memory_auditor.py` | Finds contradictions in a long-lived assistant's memory store — the failure where a rule recorded in October and reversed in November both stay live. |
| `tools/swahili-eval/` | A seed evaluation set for Swahili and Sheng: 24 items, keyword criteria, red flags, and a scorer that produces a leaderboard. |

## Design decisions worth knowing

**Local first.** Speech recognition runs in the browser, spoken replies use the
browser or system voice, statements are parsed on the machine. Nothing about a
conversation is sent anywhere except the text of the message itself.

**No shell.** The device agents accept a fixed list of actions and refuse
everything else. Remote command execution on a personal laptop is not a feature,
it is a liability, even when the key guarding it is your own.

**Honest limits.** The agents only move when they are running. The phone route
needs Android, because iOS does not allow a background script of this kind.

## Status

The tools are tested against synthetic data. The device bridge and the browser
app have not yet been verified end to end on a second machine, and this README
will not pretend otherwise until they have.

## Licence

MIT.
