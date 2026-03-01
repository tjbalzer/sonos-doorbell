"""
Sonos Doorbell Service
======================
FastAPI service that plays an MP3 doorbell sound on a single Sonos speaker.
Ongoing playback is interrupted and restored after the doorbell tone.

Architecture:
  FastAPI  →  SonosController
                 ├── SoCo (UPnP)           → state queries, backup, restore
                 └── sonos-http-api (HTTP)  → clip  (write operations)

Requirements:
  pip install fastapi uvicorn soco aiohttp python-multipart mutagen
  # Also required: sonos-http-api (Node.js) must be running:
  #   npm install -g node-sonos-http-api
  #   node /usr/local/lib/node_modules/node-sonos-http-api/server.js
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field

from sonos_controller import SonosController

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger("doorbell")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# IP address of the doorbell speaker (e.g. kitchen)
SPEAKER_IP: str = "10.10.10.10"

# Directory containing MP3 ringtones.
# Must be the clips/ directory of node-sonos-http-api — the API loads files from there directly.
MP3_DIR = os.path.expanduser("~/node-sonos-http-api/static/clips")

# URL of the node-sonos-http-api instance (typically port 5005)
SONOS_HTTP_API_URL = "http://localhost:5005"

# Default playback volume if none is specified
DEFAULT_VOLUME = 40

# ---------------------------------------------------------------------------
# Lifespan / App
# ---------------------------------------------------------------------------
controller: Optional[SonosController] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global controller
    log.info("Initialising SonosController ...")
    controller = SonosController(
        speaker_ip=SPEAKER_IP,
        mp3_dir=MP3_DIR,
        sonos_http_api_url=SONOS_HTTP_API_URL,
    )
    await controller.discover()
    log.info("SonosController ready.")
    yield
    log.info("Shutting down SonosController.")
    await controller.shutdown()


app = FastAPI(
    title="Sonos Doorbell API",
    description="Plays MP3 doorbell sounds on the configured Sonos speaker.",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RingRequest(BaseModel):
    filename: str = Field(
        ...,
        example="doorbell.mp3",
        description="MP3 filename (must be in the clips/ directory of node-sonos-http-api)",
    )
    volume: int = Field(
        DEFAULT_VOLUME,
        ge=1,
        le=100,
        description="Playback volume 1–100",
    )


class RingResponse(BaseModel):
    status: str
    message: str
    speaker: str


class StatusResponse(BaseModel):
    speakers: dict[str, dict]
    available_ringtones: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/ring", response_model=RingResponse, summary="Trigger doorbell")
async def ring(request: RingRequest, background_tasks: BackgroundTasks):
    """
    Plays the specified MP3 ringtone on the configured speaker.
    If music is currently playing, it is interrupted and resumed afterwards.
    """
    if controller is None:
        raise HTTPException(status_code=503, detail="Controller not initialised")

    mp3_path = os.path.join(MP3_DIR, request.filename)
    if not os.path.isfile(mp3_path):
        available = [f for f in os.listdir(MP3_DIR) if f.endswith(".mp3")]
        raise HTTPException(
            status_code=404,
            detail=f"File '{request.filename}' not found. Available: {available}",
        )

    background_tasks.add_task(
        controller.play_doorbell,
        filename=request.filename,
        volume=request.volume,
    )

    return RingResponse(
        status="triggered",
        message=f"Playing ringtone '{request.filename}'.",
        speaker=SPEAKER_IP,
    )


@app.get("/ring", response_model=RingResponse, summary="Trigger doorbell via GET")
async def ring_get(
    background_tasks: BackgroundTasks,
    filename: str = Query("doorbell.mp3", description="MP3 filename"),
    volume: int = Query(DEFAULT_VOLUME, ge=1, le=100, description="Playback volume 1–100"),
):
    if controller is None:
        raise HTTPException(status_code=503, detail="Controller not initialised")

    mp3_path = os.path.join(MP3_DIR, filename)
    if not os.path.isfile(mp3_path):
        available = [f for f in os.listdir(MP3_DIR) if f.endswith(".mp3")]
        raise HTTPException(
            status_code=404,
            detail=f"File '{filename}' not found. Available: {available}",
        )

    background_tasks.add_task(
        controller.play_doorbell,
        filename=filename,
        volume=volume,
    )

    return RingResponse(
        status="triggered",
        message=f"Playing ringtone '{filename}'.",
        speaker=SPEAKER_IP,
    )


@app.get("/status", response_model=StatusResponse, summary="Speaker status")
async def status():
    """Returns the current playback state of the configured speaker."""
    if controller is None:
        raise HTTPException(status_code=503, detail="Controller not initialised")

    speaker_states = await controller.get_all_states()
    ringtones = sorted(f for f in os.listdir(MP3_DIR) if f.endswith(".mp3"))
    return StatusResponse(speakers=speaker_states, available_ringtones=ringtones)


@app.get("/ringtones", summary="List available ringtones")
async def list_ringtones():
    """Lists all MP3 files in the clips/ directory."""
    files = sorted(f for f in os.listdir(MP3_DIR) if f.endswith(".mp3"))
    return {"ringtones": files}


@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok", "timestamp": time.time()}


# ---------------------------------------------------------------------------
# Direct start
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
