# sonos-doorbell
Play doorbell file via Sonos speakers via http request/API.

This is a result of a vibe coding session with Anthropic Claude (Sonnet 4.6).

# Sonos Doorbell Service

A lightweight FastAPI service that plays an MP3 doorbell sound on a single Sonos speaker. If music is currently playing, it is interrupted for the duration of the doorbell tone and then seamlessly restored — including playlist context.

---

## Table of Contents

- [sonos-doorbell](#sonos-doorbell)
- [Sonos Doorbell Service](#sonos-doorbell-service)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Requirements](#requirements)
    - [System](#system)
    - [Python packages](#python-packages)
    - [node-sonos-http-api](#node-sonos-http-api)
  - [Installation](#installation)
  - [Configuration](#configuration)
  - [Running the Service](#running-the-service)
    - [1. Start node-sonos-http-api](#1-start-node-sonos-http-api)
    - [2. Start the doorbell service](#2-start-the-doorbell-service)
    - [3. Run as a systemd service (optional)](#3-run-as-a-systemd-service-optional)
  - [API Reference](#api-reference)
    - [`GET /ring`](#get-ring)
    - [`POST /ring`](#post-ring)
    - [`GET /status`](#get-status)
    - [`GET /ringtones`](#get-ringtones)
    - [`GET /health`](#get-health)
  - [Architecture](#architecture)
    - [Component overview](#component-overview)
  - [How State Restore Works](#how-state-restore-works)
    - [Why queue-based restore matters](#why-queue-based-restore-matters)
  - [Known Limitations](#known-limitations)
  - [Project Structure](#project-structure)

---

## Features

- Triggers a doorbell MP3 on a configured Sonos speaker via simple HTTP GET or POST
- Interrupts ongoing playback and fully restores it afterwards (volume, queue position, playlist)
- Supports Spotify playlists started via the Sonos app (queue-based playback)
- Concurrent ring requests are queued — no overlapping playback
- Simple integration with doorbell hardware (e.g. Shelly, Fritz!Box, home automation systems)

---

## Requirements

### System

- Python 3.11+
- Node.js 18+ (for [node-sonos-http-api](https://github.com/jishi/node-sonos-http-api))
- Sonos speaker reachable on the local network

### Python packages

```bash
pip install fastapi uvicorn soco aiohttp python-multipart mutagen
```

### node-sonos-http-api

```bash
npm install -g node-sonos-http-api
```

The `clip` command — which plays a short audio file without permanently changing the playback state — is provided exclusively by node-sonos-http-api. All other operations use SoCo directly.

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/youruser/sonos-doorbell.git
cd sonos-doorbell

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install fastapi uvicorn soco aiohttp python-multipart mutagen

# 4. Place your MP3 files in the node-sonos-http-api clips directory
#    (default: ~/Projekte/node-sonos-http-api/clips/)
cp doorbell.mp3 ~/Projekte/node-sonos-http-api/clips/
```

---

## Configuration

Edit the configuration block at the top of `main.py`:

```python
# IP address of the doorbell speaker (e.g. kitchen)
SPEAKER_IP: str = "192.168.1.102"

# Directory containing MP3 ringtones.
# Must be the clips/ directory of node-sonos-http-api.
MP3_DIR = os.path.expanduser("~/Projekte/node-sonos-http-api/clips")

# URL of the running node-sonos-http-api instance
SONOS_HTTP_API_URL = "http://localhost:5005"

# Default playback volume for the doorbell (1–100)
DEFAULT_VOLUME = 40
```

> **Note:** The MP3 files must reside in the `clips/` directory of node-sonos-http-api. The API loads them from there directly — no HTTP streaming is involved.

---

## Running the Service

### 1. Start node-sonos-http-api

```bash
node /usr/local/lib/node_modules/node-sonos-http-api/server.js
```

Verify it is running:

```bash
curl http://localhost:5005/zones
```

### 2. Start the doorbell service

```bash
python3 main.py
```

Or with uvicorn directly:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 3. Run as a systemd service (optional)

A ready-to-use service file is included in the repository as `doorbell.service`.
Adjust the `User`, `Group`, and `WorkingDirectory` values to match your setup, then install it:

```bash
# Copy the service file
sudo cp doorbell.service /etc/systemd/system/

# Reload systemd and enable the service
sudo systemctl daemon-reload
sudo systemctl enable --now doorbell
```

**Useful commands:**

```bash
# Check service status
systemctl status doorbell

# Follow live log output
journalctl -u doorbell -f

# Restart after a configuration change
sudo systemctl restart doorbell

# Stop the service
sudo systemctl stop doorbell
```

---

## API Reference

Interactive documentation is available at `http://localhost:8000/docs` once the service is running.

### `GET /ring`

Trigger the doorbell via a simple GET request — ideal for integration with doorbell hardware or home automation systems.

**Query parameters:**

| Parameter  | Type    | Default        | Description             |
|------------|---------|----------------|-------------------------|
| `filename` | string  | `doorbell.mp3` | MP3 filename in clips/  |
| `volume`   | integer | `40`           | Playback volume (1–100) |

**Example:**

```bash
curl "http://localhost:8000/ring?filename=doorbell.mp3&volume=35"
```

**Response:**

```json
{
  "status": "triggered",
  "message": "Playing ringtone 'doorbell.mp3'.",
  "speaker": "192.168.1.102"
}
```

---

### `POST /ring`

Trigger the doorbell with a JSON body.

**Request body:**

```json
{
  "filename": "doorbell.mp3",
  "volume": 35
}
```

**Example:**

```bash
curl -X POST http://localhost:8000/ring \
  -H "Content-Type: application/json" \
  -d '{"filename": "doorbell.mp3", "volume": 35}'
```

---

### `GET /status`

Returns the current playback state of the configured speaker and lists available ringtones.

```bash
curl http://localhost:8000/status
```

```json
{
  "speakers": {
    "192.168.1.102": {
      "name": "Kitchen",
      "state": "PLAYING",
      "volume": 35,
      "title": "Wicked Ones",
      "artist": "Dorothy"
    }
  },
  "available_ringtones": ["doorbell.mp3", "acdc-hells_bells.mp3"]
}
```

---

### `GET /ringtones`

Lists all available MP3 files in the clips directory.

```bash
curl http://localhost:8000/ringtones
```

---

### `GET /health`

Simple health check endpoint.

```bash
curl http://localhost:8000/health
# {"status": "ok", "timestamp": 1740000000.0}
```

---

## Architecture

The service uses a hybrid approach combining two libraries:

| Library                        | Role                              |
|--------------------------------|-----------------------------------|
| **SoCo** (UPnP)                | Read state, backup, restore       |
| **node-sonos-http-api** (HTTP) | Play the doorbell clip            |

SoCo provides direct, structured access to the Sonos UPnP stack and is used for all read and restore operations. node-sonos-http-api is used exclusively for the `clip` command, which handles the interruption of playback internally and blocks until the clip has finished — making it the most reliable method for write operations.

### Component overview

```
HTTP Client (doorbell hardware / browser)
        │
        ▼
  FastAPI  (main.py)
        │
        ▼
  SonosController  (sonos_controller.py)
    ├── SoCo  ──────────────────────▶  Sonos Speaker (UPnP / port 1400)
    │   • save transport state
    │   • save volume + queue position
    │   • restore volume
    │   • restore queue position (play_from_queue + seek)
    │
    └── SonosHttpApiClient ─────────▶  node-sonos-http-api (HTTP / port 5005)
        • clip (play doorbell sound)        │
                                            ▼
                                      Sonos Speaker
```

---

## How State Restore Works

```
1. SAVE STATE  (SoCo)
   ├── Transport state  (PLAYING / PAUSED / STOPPED)
   ├── Volume
   ├── AV Transport URI
   │     x-rincon-queue:...    →  Sonos queue (Spotify via Sonos app, local music)
   │     x-sonos-spotify:...   →  Direct Spotify stream (Spotify app / Connect)
   └── Queue position + elapsed track time

2. PLAY CLIP  (node-sonos-http-api)
   └── Blocks until the MP3 has finished playing (~clip duration)

3. RESTORE STATE  (SoCo)
   ├── Restore volume
   └── If was playing:
         x-rincon-queue  →  play_from_queue(saved position) + seek(saved time)
                            Full playlist context preserved ✓
         x-sonos-spotify →  play_uri(saved URI)
                            Current track restarts; playlist context lost ✗
```

### Why queue-based restore matters

When a Spotify playlist is started via the **Sonos app**, Sonos loads the playlist into its internal queue and uses `x-rincon-queue:` as the AV Transport URI. The full queue — including shuffle order and upcoming tracks — is maintained on the Sonos device. Restoring via `play_from_queue()` at the saved position brings back the complete playlist context.

This is why it is important to start Spotify playback through the **Sonos app** rather than the Spotify app directly. When using Spotify Connect (Spotify app → Sonos), playback runs outside the UPnP stack and the playlist context cannot be recovered without using the Spotify Web API.

---

## Known Limitations

**Spotify Connect (Spotify app → Sonos)**
Playlist context cannot be restored. The current track restarts from the beginning, and no subsequent tracks are queued. Use the Sonos app to start Spotify playback for full restore support.

**Concurrent ring requests**
A second ring request while the doorbell is already playing will wait until the first has completed (asyncio lock). Requests are not dropped, but they are not queued either — only one plays at a time.

**Seek precision**
After queue restore, the service seeks to the saved track position. Precision may vary slightly depending on Sonos firmware and the stream type.

---

## Project Structure

```
sonos-doorbell/
├── main.py                    # FastAPI app, endpoints, configuration
├── sonos_controller.py        # Doorbell orchestration, state backup/restore
├── sonos_http_api_client.py   # Async HTTP client wrapper for node-sonos-http-api
├── requirements.txt           # Python dependencies
├── doorbell.service           # systemd unit file for Ubuntu/Debian
└── README.md
```