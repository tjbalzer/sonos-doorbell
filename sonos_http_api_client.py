"""
sonos_http_api_client.py
========================
Async HTTP client for the sonos-http-api (https://github.com/jishi/node-sonos-http-api).

Wraps all calls made via the sonos-http-api:
  - clip      → play a short audio clip (with automatic pause/restore)
  - join      → add a speaker to a group
  - leave     → leave a group (returns speaker to standalone mode)
  - volume    → set volume
  - play      → start playback
  - pause     → pause playback
  - state     → get current zone state
  - zones     → list all zones/groups

Zone names correspond to room names in the Sonos app (case-insensitive).
Zone names are always URL-encoded before being passed to the API.

Prerequisite: sonos-http-api must be running locally, e.g. on port 5005.
  npm install -g node-sonos-http-api
  node /usr/local/lib/node_modules/node-sonos-http-api/server.js
"""

import asyncio
import logging
import urllib.parse
from typing import Optional

import aiohttp

log = logging.getLogger("doorbell.sonos_http_api")


class SonosHttpApiClient:
    """
    Async client for the sonos-http-api.

    All methods are error-safe — exceptions are logged but not re-raised,
    so a failed API call does not abort the doorbell flow.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:5005",
        timeout: float = 10.0,
        clip_timeout: float = 120.0,
        leave_timeout: float = 30.0,
    ):
        """
        Args:
            base_url:      URL of the sonos-http-api, e.g. "http://localhost:5005"
            timeout:       Request timeout for regular commands (join, state, ...)
            clip_timeout:  Timeout for clip requests — the API responds only after
                           the clip has finished playing. Default: 120s.
            leave_timeout: Timeout for leave requests — Sonos may briefly block
                           during group dissolution. Default: 30s.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.clip_timeout = aiohttp.ClientTimeout(total=clip_timeout)
        self.leave_timeout = aiohttp.ClientTimeout(total=leave_timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------
    async def open(self):
        """Opens the aiohttp session. Call once at startup."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
            log.info(f"sonos-http-api client connected: {self.base_url}")

    async def close(self):
        """Closes the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Public commands
    # ------------------------------------------------------------------
    async def clip(
        self,
        zone: str,
        clip_url: str,
        volume: int,
    ) -> bool:
        """
        Plays an audio clip on a zone.

        The sonos-http-api automatically pauses ongoing playback and resumes
        it after the clip — no manual state management required.

        Endpoint: GET /{zone}/clip/{filename}/{volume}

        Args:
            zone:      Zone name (room name in Sonos app), e.g. "Kitchen"
            clip_url:  MP3 filename in the clips/ directory of sonos-http-api
            volume:    Playback volume 1–100

        Returns:
            True on success, False on error
        """
        # Important: use clip_timeout — the API responds only after playback ends.
        path = f"/{self._encode_zone(zone)}/clip/{clip_url}/{volume}"
        return await self._get(
            path,
            description=f"clip on '{zone}'",
            timeout=self.clip_timeout,
        )

    async def join(self, zone: str, target_zone: str) -> bool:
        """
        Adds `zone` to the group of `target_zone`.

        Endpoint: GET /{zone}/join/{targetZone}

        Args:
            zone:        Speaker to add to the group
            target_zone: Group coordinator (zone name)

        Returns:
            True on success, False on error
        """
        path = f"/{self._encode_zone(zone)}/join/{self._encode_zone(target_zone)}"
        return await self._get(path, description=f"join '{zone}' → '{target_zone}'")

    async def leave(self, zone: str) -> bool:
        """
        Removes `zone` from its current group (returns it to standalone mode).

        Endpoint: GET /{zone}/leave

        Args:
            zone: Zone name

        Returns:
            True on success, False on error
        """
        path = f"/{self._encode_zone(zone)}/leave"
        return await self._get(
            path,
            description=f"leave '{zone}'",
            timeout=self.leave_timeout,
        )

    async def set_volume(self, zone: str, volume: int) -> bool:
        """
        Sets the volume of a zone.

        Endpoint: GET /{zone}/volume/{volume}

        Args:
            zone:   Zone name
            volume: Volume 1–100

        Returns:
            True on success, False on error
        """
        path = f"/{self._encode_zone(zone)}/volume/{volume}"
        return await self._get(path, description=f"volume '{zone}' → {volume}")

    async def play(self, zone: str) -> bool:
        """
        Starts playback on a zone.

        Endpoint: GET /{zone}/play
        """
        path = f"/{self._encode_zone(zone)}/play"
        return await self._get(path, description=f"play '{zone}'")

    async def pause(self, zone: str) -> bool:
        """
        Pauses playback on a zone.

        Endpoint: GET /{zone}/pause
        """
        path = f"/{self._encode_zone(zone)}/pause"
        return await self._get(path, description=f"pause '{zone}'")

    async def state(self, zone: str) -> Optional[dict]:
        """
        Retrieves the current state of a zone from the sonos-http-api.

        Endpoint: GET /{zone}/state

        Returns:
            Dict with state information, or None on error
        """
        path = f"/{self._encode_zone(zone)}/state"
        return await self._get_json(path, description=f"state '{zone}'")

    async def zones(self) -> Optional[list]:
        """
        Returns all current zones/groups.

        Endpoint: GET /zones
        """
        return await self._get_json("/zones", description="zones")

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------
    def _encode_zone(self, zone: str) -> str:
        """URL-encodes the zone name (spaces → %20 etc.)."""
        return urllib.parse.quote(zone, safe="")

    async def _get(
        self,
        path: str,
        description: str = "",
        timeout: aiohttp.ClientTimeout = None,
    ) -> bool:
        """Executes a GET request. Returns True on HTTP 200."""
        url = self.base_url + path
        t = timeout or self.timeout
        try:
            async with self._session.get(url, timeout=t) as resp:
                body = await resp.text()
                if resp.status == 200:
                    log.info(f"  ✓ {description}: {body.strip()[:80]}")
                    return True
                else:
                    log.warning(
                        f"  ✗ {description}: HTTP {resp.status} — {body.strip()[:120]}"
                    )
                    return False
        except aiohttp.ClientConnectorError:
            log.error(
                f"  ✗ {description}: sonos-http-api unreachable ({self.base_url})"
            )
            return False
        except asyncio.TimeoutError:
            log.error(f"  ✗ {description}: timeout after {t.total}s")
            return False
        except Exception as exc:
            log.error(f"  ✗ {description}: unexpected error — {exc}")
            return False

    async def _get_json(self, path: str, description: str = "") -> Optional[dict | list]:
        """Executes a GET request and returns the JSON body."""
        url = self.base_url + path
        try:
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    log.debug(f"  ✓ {description}: {str(data)[:120]}")
                    return data
                else:
                    body = await resp.text()
                    log.warning(
                        f"  ✗ {description}: HTTP {resp.status} — {body.strip()[:120]}"
                    )
                    return None
        except Exception as exc:
            log.error(f"  ✗ {description}: {exc}")
            return None