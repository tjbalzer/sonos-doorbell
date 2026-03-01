"""
sonos_controller.py
===================
Doorbell controller for a single Sonos speaker.

Flow:
  1. SoCo:           Save state (transport state, volume, URI)
  2. sonos-http-api: Play clip — interrupts ongoing playback internally
                     and blocks until the clip has finished
  3. SoCo:           Restore volume
  4. SoCo:           Restore playback (queue position or stream URI)
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from soco import SoCo

from sonos_http_api_client import SonosHttpApiClient

log = logging.getLogger("doorbell.controller")


# ---------------------------------------------------------------------------
# Data model: saved speaker state
# ---------------------------------------------------------------------------
@dataclass
class SonosState:
    ip: str
    zone_name: str = ""
    was_playing: bool = False
    volume: int = 20
    transport_state: str = "STOPPED"        # PLAYING | PAUSED_PLAYBACK | STOPPED
    av_transport_uri: str = ""
    av_transport_uri_metadata: str = ""
    queue_position: int = 1
    track_position: str = "0:00:00"         # HH:MM:SS for seek
    spotify_via_api: bool = False           # True if URI was retrieved via sonos-http-api


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class SonosController:
    """
    Controls a single Sonos speaker for the doorbell function.

    SoCo            → state queries, backup, restore  (read)
    sonos-http-api  → clip                            (write)
    """

    def __init__(
        self,
        speaker_ip: str,
        mp3_dir: str,
        sonos_http_api_url: str = "http://localhost:5005",
    ):
        """
        Args:
            speaker_ip:          IP address of the doorbell speaker (e.g. kitchen)
            mp3_dir:             Path to the clips/ directory of node-sonos-http-api
            sonos_http_api_url:  URL of the sonos-http-api instance
        """
        self.speaker_ip = speaker_ip
        self.mp3_dir = mp3_dir
        self._device: Optional[SoCo] = None
        self._zone_name: str = ""
        self._lock = asyncio.Lock()
        self.http_api = SonosHttpApiClient(base_url=sonos_http_api_url)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    async def discover(self):
        """Connects to the speaker via SoCo and opens the HTTP API client."""
        await self.http_api.open()

        loop = asyncio.get_event_loop()
        try:
            device = await loop.run_in_executor(None, SoCo, self.speaker_ip)
            name = await loop.run_in_executor(None, lambda: device.player_name)
            self._device = device
            self._zone_name = name
            log.info(f"  ✓ SoCo connected: {name} ({self.speaker_ip})")
        except Exception as exc:
            raise RuntimeError(
                f"Speaker {self.speaker_ip} unreachable: {exc}"
            )

    async def shutdown(self):
        """Closes the HTTP client cleanly."""
        await self.http_api.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def play_doorbell(
        self,
        filename: str,
        volume: int,
        duration_seconds=None,
        speaker_ips=None,   # ignored, kept for API compatibility
    ):
        """
        Plays the doorbell tone and restores the previous state afterwards.
        asyncio.Lock prevents concurrent executions.
        """
        async with self._lock:
            if self._device is None:
                log.error("Speaker not initialised.")
                return

            # ── 1. Save state (SoCo) ───────────────────────────────────
            log.info(f"── 1/3  Saving state '{self._zone_name}' (SoCo) ...")
            state = await self._save_state()

            try:
                # ── 2. Play clip (sonos-http-api) ──────────────────────
                # clip blocks until the audio has finished playing.
                log.info(
                    f"── 2/3  Clip '{filename}' (vol={volume}) "
                    f"on '{self._zone_name}' ..."
                )
                result = await self.http_api.clip(self._zone_name, filename, volume)
                if result:
                    log.info(f"  ▶ Clip played")
                else:
                    log.warning(f"  ? Clip not confirmed")

                log.info("   Waiting 0.5s buffer ...")
                await asyncio.sleep(0.5)

            except Exception as exc:
                log.error(f"Error during clip: {exc}")
            finally:
                # ── 3. Restore state ───────────────────────────────────
                log.info(f"── 3/3  Restoring state ...")
                await self._restore_state(state)

    async def get_all_states(self) -> dict:
        """Returns the current playback status of the speaker (SoCo)."""
        if self._device is None:
            return {}
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(
                None, self._device.get_current_transport_info
            )
            track = await loop.run_in_executor(
                None, self._device.get_current_track_info
            )
            vol = await loop.run_in_executor(None, lambda: self._device.volume)
            return {
                self.speaker_ip: {
                    "name": self._zone_name,
                    "state": info.get("current_transport_state", "UNKNOWN"),
                    "volume": vol,
                    "title": track.get("title", ""),
                    "artist": track.get("artist", ""),
                }
            }
        except Exception as exc:
            return {self.speaker_ip: {"name": self._zone_name, "error": str(exc)}}

    # ------------------------------------------------------------------
    # Save state (SoCo)
    # ------------------------------------------------------------------
    async def _save_state(self) -> SonosState:
        """Saves the full state of the speaker via SoCo."""
        loop = asyncio.get_event_loop()
        state = SonosState(ip=self.speaker_ip, zone_name=self._zone_name)

        try:
            # Transport state
            ti = await loop.run_in_executor(
                None, self._device.get_current_transport_info
            )
            state.transport_state = ti.get("current_transport_state", "STOPPED")
            state.was_playing = state.transport_state == "PLAYING"

            # Volume
            state.volume = await loop.run_in_executor(
                None, lambda: self._device.volume
            )

            # Track info (queue position + elapsed time)
            track = await loop.run_in_executor(
                None, self._device.get_current_track_info
            )
            state.queue_position = int(track.get("playlist_position", 1) or 1)
            state.track_position = track.get("position", "0:00:00") or "0:00:00"

            # AV Transport URI + metadata
            # media_info returns the real transport URI — for Spotify via the Sonos app
            # this is x-rincon-queue:..., not the individual track URI.
            media = await loop.run_in_executor(
                None, self._device.get_current_media_info
            )
            # SoCo returns the key as 'uri' or 'current_uri' depending on version
            state.av_transport_uri = (
                media.get("uri", "") or media.get("current_uri", "")
            )
            state.av_transport_uri_metadata = media.get("current_uri_metadata", "")

            # Fallback only if media_info is empty (e.g. Spotify Connect)
            if not state.av_transport_uri and state.was_playing:
                api_state = await self.http_api.state(self._zone_name)
                if api_state:
                    uri = api_state.get("currentTrack", {}).get("uri", "")
                    if uri:
                        state.av_transport_uri = uri
                        state.spotify_via_api = True
                        log.info(f"  URI fallback via sonos-http-api: {uri[:60]}")

            log.info(
                f"  ✓ State={state.transport_state}, vol={state.volume}, "
                f"URI={state.av_transport_uri[:60] if state.av_transport_uri else '–'}"
            )

        except Exception as exc:
            log.warning(f"  ✗ State backup failed: {exc}")

        return state

    # ------------------------------------------------------------------
    # Restore state (SoCo)
    # ------------------------------------------------------------------
    async def _restore_state(self, state: SonosState):
        """Restores the speaker state."""
        loop = asyncio.get_event_loop()

        # Restore volume
        try:
            await loop.run_in_executor(
                None, lambda: setattr(self._device, "volume", state.volume)
            )
            log.info(f"  ✓ Volume: {state.volume}")
        except Exception as exc:
            log.warning(f"  ✗ Volume restore failed: {exc}")

        if not state.was_playing:
            log.info(f"  ⏹ Was stopped — no playback restore needed")
            return

        if not state.av_transport_uri and not state.spotify_via_api:
            log.warning(f"  ? No URI saved — skipping playback restore")
            return

        # Queue playback (local music, Spotify via Sonos app)
        if "x-rincon-queue" in state.av_transport_uri:
            await self._restore_queue(loop, state)
            return

        # Spotify Connect / direct stream
        await self._restore_stream(loop, state)

    async def _restore_queue(self, loop, state: SonosState):
        """Restores queue-based playback."""
        pos = max(1, state.queue_position)
        try:
            await loop.run_in_executor(
                None, lambda: self._device.play_from_queue(pos - 1)
            )
            if state.track_position and state.track_position != "0:00:00":
                try:
                    await loop.run_in_executor(
                        None, lambda: self._device.seek(state.track_position)
                    )
                except Exception:
                    pass
            await loop.run_in_executor(None, self._device.play)
            log.info(f"  ✓ Queue: position {pos}, time {state.track_position}")
        except Exception as exc:
            log.warning(f"  ✗ Queue restore failed: {exc}")

    async def _restore_stream(self, loop, state: SonosState):
        """Restores Spotify Connect or direct stream playback via play_uri."""
        try:
            await loop.run_in_executor(
                None,
                lambda: self._device.play_uri(
                    state.av_transport_uri,
                    meta=state.av_transport_uri_metadata,
                ),
            )
            log.info(f"  ✓ Stream: {state.av_transport_uri[:60]}")
        except Exception as exc:
            log.warning(f"  ✗ Stream restore failed: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _get_mp3_duration(self, filename: str) -> float:
        mp3_path = os.path.join(self.mp3_dir, filename)
        loop = asyncio.get_event_loop()

        def _read():
            try:
                from mutagen.mp3 import MP3
                return MP3(mp3_path).info.length
            except ImportError:
                log.warning("mutagen not installed — estimating duration from file size")
                return max(3.0, os.path.getsize(mp3_path) / 16_000)
            except Exception as exc:
                log.warning(f"Could not determine duration: {exc} — using 10s")
                return 10.0

        return await loop.run_in_executor(None, _read)
