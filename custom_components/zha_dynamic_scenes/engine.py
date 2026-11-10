"""Start colours, lazily stored scene slots and the dynamic palette start."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
import contextlib
from dataclasses import dataclass
import itertools
import logging
from typing import Any, Protocol

from .models import Lamp, LightAction, Palette, PaletteColor
from .protocol import (
    CMD_ADD_SCENE,
    CMD_REMOVE_SCENE,
    STATUS_INSUFFICIENT_SPACE,
    STATUS_SUCCESS,
    SceneContent,
    add_scene_payload,
    dynamic_palette_payload,
    remove_scene_payload,
)

_LOGGER = logging.getLogger(__name__)

FIRST_SCENE_ID = 0x80
LAST_SCENE_ID = 0xFE
SCENE_TRANSITION = 4  # 1/10 s, the Hue default


class TransportError(Exception):
    """A frame could not be delivered."""


class SceneTransport(Protocol):
    """Sends Scenes cluster frames to lamps and groups."""

    async def async_scene_request(self, lamp: Lamp, command_id: int, payload: bytes) -> int:
        """
        Send a standard Scenes cluster command to one lamp.

        :return: ZCL status of the lamp's response.
        :raises TransportError: The lamp did not answer.
        """

    async def async_start_dynamic(self, group_id: int, payload: bytes) -> None:
        """
        Groupcast the dynamic palette command to a Zigbee group.

        :raises TransportError: The frame could not be sent.
        """


@dataclass(frozen=True, slots=True)
class LampTarget:
    """
    Start state of a lamp.

    :param xy: Start colour.
    :param brightness: Brightness in percent (0..100).
    :param mirek: Colour temperature for lamps without colour, if the palette has one.
    """

    xy: tuple[float, float]
    brightness: float
    mirek: int | None = None


def assign_targets(
    lamps: Sequence[Lamp], clusters: Sequence[Sequence[str]], palette: Palette
) -> dict[str, LampTarget]:
    """
    Return the start state of every lamp, keyed by IEEE.

    Lamps of a cluster share one start state; every other lamp is a cluster of its own.
    A cluster takes the Hue scene action of its first lamp that has one, the other clusters
    get the palette colours in turn. The result only depends on the input, so lamps keep
    their start colour across activations.

    :param lamps: Lamps of the zone.
    :param clusters: Lists of IEEEs that must share a start colour.
    :param palette: The palette to start.
    """
    known = {lamp.ieee for lamp in lamps}
    units: list[list[str]] = []
    clustered: set[str] = set()
    for cluster in clusters:
        if members := [ieee for ieee in cluster if ieee in known - clustered]:
            units.append(members)
            clustered.update(members)
    units.extend(
        [lamp.ieee]
        for lamp in sorted(lamps, key=lambda lamp: lamp.entity_id)
        if lamp.ieee not in clustered
    )

    spread = itertools.cycle(palette.colors)
    targets: dict[str, LampTarget] = {}
    for unit in units:
        action = next((palette.actions[i] for i in unit if i in palette.actions), None)
        targets.update(dict.fromkeys(unit, _target(palette, action, spread)))
    return targets


class SceneCache:
    """
    Scene slots per zone and the scene content each lamp is known to store.

    The state is a JSON serializable dict; `save` is called after every change.
    """

    def __init__(self, data: dict[str, Any], save: Callable[[], None]) -> None:
        """
        Initialize the cache.

        :param data: State from `as_dict`, or an empty dict.
        :param save: Called after every change to persist `as_dict`.
        """
        # group id -> palette id -> {"scene_id": int, "used": int}
        self._slots: dict[str, dict[str, dict[str, int]]] = data.get("slots", {})
        # ieee -> "group id/scene id" -> [level, x, y]
        self._scenes: dict[str, dict[str, list[int]]] = data.get("scenes", {})
        self._save = save

    def as_dict(self) -> dict[str, Any]:
        """Return the state to persist."""
        return {"slots": self._slots, "scenes": self._scenes}

    def slot(self, group_id: int, palette_id: str) -> int | None:
        """Return the scene id of a palette in a zone, if it has one."""
        if slot := self._slots.get(str(group_id), {}).get(palette_id):
            return slot["scene_id"]
        return None

    def allocate(self, group_id: int, palette_id: str) -> int:
        """
        Give a palette a scene id in a zone.

        Takes the lowest free id, or the id of the least recently used palette when all
        are taken; its scene is overwritten when the new palette is stored.
        """
        slots = self._slots.setdefault(str(group_id), {})
        used = {slot["scene_id"] for slot in slots.values()}
        scene_id = next(
            (i for i in range(FIRST_SCENE_ID, LAST_SCENE_ID + 1) if i not in used), None
        )
        if scene_id is None:
            scene_id = self.release(group_id, min(slots, key=lambda p: slots[p]["used"]))
        slots[palette_id] = {"scene_id": scene_id, "used": 0}
        self._save()
        return scene_id

    def least_recently_used(self, group_id: int, exclude: str) -> str | None:
        """Return the palette id of the zone's least recently used slot other than `exclude`."""
        slots = self._slots.get(str(group_id), {})
        candidates = [palette_id for palette_id in slots if palette_id != exclude]
        return min(candidates, key=lambda p: slots[p]["used"], default=None)

    def touch(self, group_id: int, palette_id: str) -> None:
        """Mark a palette's slot as the zone's most recently used one."""
        slots = self._slots[str(group_id)]
        slots[palette_id]["used"] = max(slot["used"] for slot in slots.values()) + 1
        self._save()

    def release(self, group_id: int, palette_id: str) -> int:
        """Drop a palette's slot and everything known about it; return its scene id."""
        scene_id = self._slots[str(group_id)].pop(palette_id)["scene_id"]
        key = _scene_key(group_id, scene_id)
        for scenes in self._scenes.values():
            scenes.pop(key, None)
        self._save()
        return scene_id

    def stored(self, ieee: str, group_id: int, scene_id: int) -> SceneContent | None:
        """Return what a lamp stores for a scene, None if unknown."""
        if content := self._scenes.get(ieee, {}).get(_scene_key(group_id, scene_id)):
            return SceneContent(*content)
        return None

    def set_stored(
        self, ieee: str, group_id: int, scene_id: int, content: SceneContent | None
    ) -> None:
        """Record what a lamp stores for a scene; None marks it unknown."""
        key = _scene_key(group_id, scene_id)
        if content is None:
            self._scenes.get(ieee, {}).pop(key, None)
        else:
            self._scenes.setdefault(ieee, {})[key] = [content.level, content.x, content.y]
        self._save()

    def forget_lamp(self, ieee: str) -> None:
        """Forget all scenes of a lamp, e.g. after it was reset."""
        if self._scenes.pop(ieee, None) is not None:
            self._save()

    def forget_zone(self, group_id: int) -> None:
        """Forget what the lamps store for a zone's scenes, so they are written again."""
        prefix = f"{group_id}/"
        for scenes in self._scenes.values():
            for key in [key for key in scenes if key.startswith(prefix)]:
                del scenes[key]
        self._save()


class SceneEngine:
    """Stores palette scenes in the lamps when needed and starts the palette."""

    def __init__(self, transport: SceneTransport, cache: SceneCache) -> None:
        """Initialize the engine."""
        self._transport = transport
        self._cache = cache
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def async_start(
        self,
        group_id: int,
        palette: Palette,
        contents: Sequence[tuple[Lamp, SceneContent]],
        *,
        speed: float,
        brightness: int | None = None,
        transition: int | None = None,
    ) -> list[Lamp]:
        """
        Start a palette on a zone.

        Writes the palette's scene to the lamps whose stored content is unknown or
        different, then sends one groupcast so all lamps start together.

        :param group_id: Zigbee group of the zone.
        :param palette: The palette to start.
        :param contents: The dynamic lamps of the zone with the scene content they start from.
        :param speed: Animation speed 0..1.
        :param brightness: Level 1..254 for all lamps instead of their own.
        :param transition: Transition time in 1/10 s.
        :return: Lamps that could not store the scene and will not animate.
        :raises TransportError: The groupcast could not be sent.
        """
        async with self._locks[group_id]:
            lamps = [lamp for lamp, _ in contents]
            scene_id = self._cache.slot(group_id, palette.palette_id)
            if scene_id is None:
                scene_id = self._cache.allocate(group_id, palette.palette_id)
            pending = [
                (lamp, content)
                for lamp, content in contents
                if self._cache.stored(lamp.ieee, group_id, scene_id) != content
            ]
            failed = await self._async_write(group_id, palette.palette_id, scene_id, pending, lamps)
            self._cache.touch(group_id, palette.palette_id)
            await self._transport.async_start_dynamic(
                group_id,
                dynamic_palette_payload(
                    group_id,
                    scene_id,
                    [color.xy for color in palette.colors],
                    speed=speed,
                    brightness=brightness,
                    transition=transition,
                ),
            )
            return failed

    async def _async_write(
        self,
        group_id: int,
        palette_id: str,
        scene_id: int,
        pending: Sequence[tuple[Lamp, SceneContent]],
        lamps: Sequence[Lamp],
    ) -> list[Lamp]:
        """Add the scene to the pending lamps; return the lamps that failed."""
        statuses = list(
            await asyncio.gather(
                *(self._async_add(lamp, group_id, scene_id, content) for lamp, content in pending)
            )
        )
        full = [i for i, status in enumerate(statuses) if status == STATUS_INSUFFICIENT_SPACE]
        if full and (victim := self._cache.least_recently_used(group_id, exclude=palette_id)):
            await self._async_evict(group_id, victim, lamps)
            retried = await asyncio.gather(
                *(self._async_add(pending[i][0], group_id, scene_id, pending[i][1]) for i in full)
            )
            for i, status in zip(full, retried, strict=True):
                statuses[i] = status

        failed: list[Lamp] = []
        for (lamp, _), status in zip(pending, statuses, strict=True):
            if status != STATUS_SUCCESS:
                _LOGGER.warning(
                    "%s could not store scene 0x%02x of group 0x%04x (%s)",
                    lamp.entity_id,
                    scene_id,
                    group_id,
                    "no response" if status is None else f"status 0x{status:02x}",
                )
                failed.append(lamp)
        return failed

    async def _async_add(
        self, lamp: Lamp, group_id: int, scene_id: int, content: SceneContent
    ) -> int | None:
        """Add the scene to one lamp; return the ZCL status, None without response."""
        try:
            status = await self._transport.async_scene_request(
                lamp,
                CMD_ADD_SCENE,
                add_scene_payload(group_id, scene_id, content, SCENE_TRANSITION),
            )
        except TransportError:
            status = None
        self._cache.set_stored(
            lamp.ieee, group_id, scene_id, content if status == STATUS_SUCCESS else None
        )
        return status

    async def _async_evict(self, group_id: int, palette_id: str, lamps: Sequence[Lamp]) -> None:
        """Remove a palette's scene from the zone's lamps and free its slot."""
        scene_id = self._cache.release(group_id, palette_id)
        _LOGGER.debug("Evicting scene 0x%02x of group 0x%04x", scene_id, group_id)
        await asyncio.gather(*(self._async_remove(lamp, group_id, scene_id) for lamp in lamps))

    async def _async_remove(self, lamp: Lamp, group_id: int, scene_id: int) -> None:
        """Remove a scene from one lamp; a lamp without the scene or response is fine."""
        with contextlib.suppress(TransportError):
            await self._transport.async_scene_request(
                lamp, CMD_REMOVE_SCENE, remove_scene_payload(group_id, scene_id)
            )


def _target(
    palette: Palette, action: LightAction | None, spread: Iterator[PaletteColor]
) -> LampTarget:
    """Return the start state for a Hue scene action, or the next palette colour."""
    if action is not None and action.xy is not None:
        xy, brightness = action.xy, palette.colors[0].brightness
    else:
        color = next(spread)
        xy, brightness = color.xy, color.brightness
    mirek = palette.color_temperature.mirek if palette.color_temperature else None
    if action is not None:
        brightness = action.brightness if action.brightness is not None else brightness
        mirek = action.mirek or mirek
    return LampTarget(xy=xy, brightness=brightness, mirek=mirek)


def _scene_key(group_id: int, scene_id: int) -> str:
    """Return the cache key of a scene."""
    return f"{group_id}/{scene_id}"
