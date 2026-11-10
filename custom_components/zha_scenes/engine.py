"""Start colours, lazily stored scene slots and the dynamic palette start."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Sequence
import contextlib
from dataclasses import dataclass
import itertools
import logging
from typing import Any, Protocol

from .models import Gradient, Lamp, Palette, ZoneConfig
from .protocol import (
    CMD_ADD_HUE_SCENE,
    CMD_ENHANCED_ADD_SCENE,
    CMD_REMOVE_SCENE,
    FEATURE_GRADIENT,
    MANUFACTURER_SIGNIFY,
    STATUS_INSUFFICIENT_SPACE,
    STATUS_SUCCESS,
    GradientSceneContent,
    SceneContent,
    add_scene_payload,
    dynamic_palette_payload,
    gradient_scene_payload,
    remove_scene_payload,
)

_LOGGER = logging.getLogger(__name__)

FIRST_SCENE_ID = 0x80
LAST_SCENE_ID = 0xFE
# like the Hue bridge: scenes of a real group would be lost when a lamp leaves that group
# or when a Remove All Scenes is sent to it
SCENE_GROUP = 0x0000
SCENE_TRANSITION = 4  # 1/10 s, the Hue default
MAX_GRADIENT_POINTS = 5  # what current Hue gradient lamps take

type StoredContent = SceneContent | GradientSceneContent


class TransportError(Exception):
    """A frame could not be delivered."""


class SceneTransport(Protocol):
    """Sends Scenes cluster frames to lamps and groups."""

    async def async_scene_request(
        self, lamp: Lamp, command_id: int, payload: bytes, *, manufacturer: int | None = None
    ) -> int:
        """
        Send a Scenes cluster command to one lamp.

        :param manufacturer: Manufacturer code of a manufacturer specific command.
        :return: ZCL status of the lamp's response.
        :raises TransportError: The lamp did not answer.
        """

    async def async_read_features(self, lamp: Lamp) -> int:
        """
        Read the Hue features bitmap of one lamp.

        :raises TransportError: The lamp did not answer or did not report the bitmap.
        """

    async def async_start_dynamic(self, group_id: int, payload: bytes) -> None:
        """
        Groupcast the dynamic palette command to a Zigbee group.

        :raises TransportError: The frame could not be sent.
        """

    async def async_start_dynamic_lamp(self, lamp: Lamp, payload: bytes) -> None:
        """
        Unicast the dynamic palette command to one lamp.

        :raises TransportError: The frame could not be sent.
        """


@dataclass(frozen=True, slots=True)
class LampTarget:
    """
    Start state of a lamp.

    :param xy: Start colour.
    :param brightness: Brightness in percent (0..100).
    :param gradient: Start gradient for gradient lamps.
    :param mirek: Colour temperature for lamps without colour, if the palette has one.
    """

    xy: tuple[float, float]
    brightness: float
    gradient: Gradient
    mirek: int | None = None


def assign_targets(
    lamps: Sequence[Lamp], clusters: Sequence[Sequence[str]], palette: Palette
) -> dict[str, LampTarget]:
    """
    Return the start state of every lamp, keyed by IEEE.

    Lamps of a cluster share one start state; every other lamp is a cluster of its own.
    The clusters get the palette colours in turn and gradient lamps show the palette
    colours as gradient. The result only depends on the input, so lamps keep their start
    colour across activations.

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
    palette_gradient = Gradient(
        points=tuple(color.xy for color in palette.colors[:MAX_GRADIENT_POINTS])
    )
    mirek = palette.color_temperature.mirek if palette.color_temperature else None
    targets: dict[str, LampTarget] = {}
    for unit in units:
        color = next(spread)
        target = LampTarget(
            xy=color.xy, brightness=color.brightness, gradient=palette_gradient, mirek=mirek
        )
        targets.update(dict.fromkeys(unit, target))
    return targets


class SceneCache:
    """
    Scene slots per zone, the scene content each lamp is known to store and its gradient support.

    All scenes are stored in group 0x0000 and a lamp can be in several zones, so scene ids
    are unique over all zones. The state is a JSON serializable dict; `save` is called
    after every change.
    """

    def __init__(self, data: dict[str, Any], save: Callable[[], None]) -> None:
        """
        Initialize the cache.

        :param data: State from `as_dict`, or an empty dict.
        :param save: Called after every change to persist `as_dict`.
        """
        # zone id -> palette id -> {"scene_id": int, "used": int}
        self._slots: dict[str, dict[str, dict[str, int]]] = data.get("slots", {})
        # ieee -> scene id -> [level, x, y] or {"level": int, "points": [[x, y]], "style": int}
        self._scenes: dict[str, dict[str, list[int] | dict[str, Any]]] = data.get("scenes", {})
        # ieee -> whether the lamp shows gradients
        self._gradient: dict[str, bool] = data.get("gradient", {})
        self._save = save

    def as_dict(self) -> dict[str, Any]:
        """Return the state to persist."""
        return {"slots": self._slots, "scenes": self._scenes, "gradient": self._gradient}

    def slot(self, zone_id: str, palette_id: str) -> int | None:
        """Return the scene id of a palette in a zone, if it has one."""
        if slot := self._slots.get(zone_id, {}).get(palette_id):
            return slot["scene_id"]
        return None

    def allocate(self, zone_id: str, palette_id: str) -> int:
        """
        Give a palette a scene id in a zone.

        Takes the lowest free id, or the id of the least recently used palette of any zone
        when all are taken; its scene is overwritten when the new palette is stored.
        """
        used = {slot["scene_id"] for slots in self._slots.values() for slot in slots.values()}
        scene_id = next(
            (i for i in range(FIRST_SCENE_ID, LAST_SCENE_ID + 1) if i not in used), None
        )
        if scene_id is None:
            zone, palette = min(
                ((zone, palette) for zone, slots in self._slots.items() for palette in slots),
                key=lambda key: self._slots[key[0]][key[1]]["used"],
            )
            scene_id = self.release(zone, palette)
        self._slots.setdefault(zone_id, {})[palette_id] = {"scene_id": scene_id, "used": 0}
        self._save()
        return scene_id

    def least_recently_used(self, zone_id: str, exclude: str) -> str | None:
        """Return the palette id of the zone's least recently used slot other than `exclude`."""
        slots = self._slots.get(zone_id, {})
        candidates = [palette_id for palette_id in slots if palette_id != exclude]
        return min(candidates, key=lambda p: slots[p]["used"], default=None)

    def touch(self, zone_id: str, palette_id: str) -> None:
        """Mark a palette's slot as the most recently used one."""
        latest = max(slot["used"] for slots in self._slots.values() for slot in slots.values())
        self._slots[zone_id][palette_id]["used"] = latest + 1
        self._save()

    def release(self, zone_id: str, palette_id: str) -> int:
        """Drop a palette's slot and everything known about it; return its scene id."""
        scene_id = self._slots[zone_id].pop(palette_id)["scene_id"]
        for scenes in self._scenes.values():
            scenes.pop(str(scene_id), None)
        self._save()
        return scene_id

    def stored(self, ieee: str, scene_id: int) -> StoredContent | None:
        """Return what a lamp stores for a scene, None if unknown."""
        if content := self._scenes.get(ieee, {}).get(str(scene_id)):
            return _content_from_json(content)
        return None

    def set_stored(self, ieee: str, scene_id: int, content: StoredContent | None) -> None:
        """Record what a lamp stores for a scene; None marks it unknown."""
        key = str(scene_id)
        if content is None:
            self._scenes.get(ieee, {}).pop(key, None)
        else:
            self._scenes.setdefault(ieee, {})[key] = _content_to_json(content)
        self._save()

    def gradient(self, ieee: str) -> bool | None:
        """Return whether a lamp shows gradients, None if unknown."""
        return self._gradient.get(ieee)

    def set_gradient(self, ieee: str, gradient: bool) -> None:
        """Record whether a lamp shows gradients."""
        self._gradient[ieee] = gradient
        self._save()

    def forget_lamp(self, ieee: str) -> None:
        """Forget all scenes and the gradient support of a lamp, e.g. after it was reset."""
        scenes = self._scenes.pop(ieee, None)
        gradient = self._gradient.pop(ieee, None)
        if scenes is not None or gradient is not None:
            self._save()

    def forget_zone(self, zone_id: str) -> None:
        """Forget what the lamps store for a zone's palettes, so they are written again."""
        keys = {str(slot["scene_id"]) for slot in self._slots.get(zone_id, {}).values()}
        for scenes in self._scenes.values():
            for key in keys & scenes.keys():
                del scenes[key]
        self._save()


class SceneEngine:
    """Stores palette scenes in the lamps when needed and starts the palette."""

    def __init__(self, transport: SceneTransport, cache: SceneCache) -> None:
        """Initialize the engine."""
        self._transport = transport
        self._cache = cache
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def async_gradient_lamps(self, lamps: Sequence[Lamp]) -> set[str]:
        """
        Return the IEEEs of the lamps that show gradients.

        A lamp is asked once and remembered until it is forgotten. A lamp that does not
        answer counts as one without gradient and is asked again next time.
        """
        unknown = [lamp for lamp in lamps if self._cache.gradient(lamp.ieee) is None]
        await asyncio.gather(*(self._async_read_gradient(lamp) for lamp in unknown))
        return {lamp.ieee for lamp in lamps if self._cache.gradient(lamp.ieee)}

    async def async_start(
        self,
        zone: ZoneConfig,
        palette: Palette,
        contents: Sequence[tuple[Lamp, StoredContent]],
        *,
        speed: float,
        brightness: int | None = None,
        transition: int | None = None,
    ) -> list[Lamp]:
        """
        Start a palette on a zone.

        Writes the palette's scene to the lamps whose stored content is unknown or
        different, then starts the palette with one groupcast to the zone's group, so all
        lamps start together, or with a unicast to the lamp of a single-light zone.

        :param zone: The zone to start the palette on.
        :param palette: The palette to start.
        :param contents: The dynamic lamps of the zone with the scene content they start from;
            gradient content for gradient lamps.
        :param speed: Animation speed 0..1.
        :param brightness: Level 1..254 for all lamps instead of their own.
        :param transition: Transition time in 1/10 s.
        :return: Lamps that could not store the scene and will not animate.
        :raises TransportError: The start could not be sent.
        """
        zone_id = zone.zone_id
        async with self._locks[zone_id]:
            lamps = [lamp for lamp, _ in contents]
            scene_id = self._cache.slot(zone_id, palette.palette_id)
            if scene_id is None:
                scene_id = self._cache.allocate(zone_id, palette.palette_id)
            pending = [
                (lamp, content)
                for lamp, content in contents
                if self._cache.stored(lamp.ieee, scene_id) != content
            ]
            failed = await self._async_write(zone_id, palette.palette_id, scene_id, pending, lamps)
            self._cache.touch(zone_id, palette.palette_id)
            payload = dynamic_palette_payload(
                SCENE_GROUP,
                scene_id,
                [color.xy for color in palette.colors],
                speed=speed,
                brightness=brightness,
                transition=transition,
            )
            if zone.group_id is None:
                for lamp in lamps:
                    await self._transport.async_start_dynamic_lamp(lamp, payload)
            else:
                await self._transport.async_start_dynamic(zone.group_id, payload)
            return failed

    async def _async_write(
        self,
        zone_id: str,
        palette_id: str,
        scene_id: int,
        pending: Sequence[tuple[Lamp, StoredContent]],
        lamps: Sequence[Lamp],
    ) -> list[Lamp]:
        """Add the scene to the pending lamps; return the lamps that failed."""
        statuses = list(
            await asyncio.gather(
                *(self._async_add(lamp, scene_id, content) for lamp, content in pending)
            )
        )
        full = [i for i, status in enumerate(statuses) if status == STATUS_INSUFFICIENT_SPACE]
        if full and (victim := self._cache.least_recently_used(zone_id, exclude=palette_id)):
            await self._async_evict(zone_id, victim, lamps)
            retried = await asyncio.gather(
                *(self._async_add(pending[i][0], scene_id, pending[i][1]) for i in full)
            )
            for i, status in zip(full, retried, strict=True):
                statuses[i] = status

        failed: list[Lamp] = []
        for (lamp, _), status in zip(pending, statuses, strict=True):
            if status != STATUS_SUCCESS:
                _LOGGER.warning(
                    "%s could not store scene 0x%02x of zone %s (%s)",
                    lamp.entity_id,
                    scene_id,
                    zone_id,
                    "no response" if status is None else f"status 0x{status:02x}",
                )
                failed.append(lamp)
        return failed

    async def _async_add(self, lamp: Lamp, scene_id: int, content: StoredContent) -> int | None:
        """Add the scene to one lamp; return the ZCL status, None without response."""
        try:
            if isinstance(content, GradientSceneContent):
                status = await self._transport.async_scene_request(
                    lamp,
                    CMD_ADD_HUE_SCENE,
                    gradient_scene_payload(SCENE_GROUP, scene_id, content, SCENE_TRANSITION),
                    manufacturer=MANUFACTURER_SIGNIFY,
                )
            else:
                status = await self._transport.async_scene_request(
                    lamp,
                    CMD_ENHANCED_ADD_SCENE,
                    add_scene_payload(SCENE_GROUP, scene_id, content, SCENE_TRANSITION),
                )
        except TransportError:
            status = None
        self._cache.set_stored(lamp.ieee, scene_id, content if status == STATUS_SUCCESS else None)
        return status

    async def _async_evict(self, zone_id: str, palette_id: str, lamps: Sequence[Lamp]) -> None:
        """Remove a palette's scene from the zone's lamps and free its slot."""
        scene_id = self._cache.release(zone_id, palette_id)
        _LOGGER.debug("Evicting scene 0x%02x of zone %s", scene_id, zone_id)
        await asyncio.gather(*(self._async_remove(lamp, scene_id) for lamp in lamps))

    async def _async_remove(self, lamp: Lamp, scene_id: int) -> None:
        """Remove a scene from one lamp; a lamp without the scene or response is fine."""
        with contextlib.suppress(TransportError):
            await self._transport.async_scene_request(
                lamp, CMD_REMOVE_SCENE, remove_scene_payload(SCENE_GROUP, scene_id)
            )

    async def _async_read_gradient(self, lamp: Lamp) -> None:
        """Ask a lamp whether it shows gradients and remember the answer."""
        try:
            features = await self._transport.async_read_features(lamp)
        except TransportError as err:
            _LOGGER.debug("Cannot read the Hue features of %s: %s", lamp.entity_id, err)
            return
        self._cache.set_gradient(lamp.ieee, bool(features & FEATURE_GRADIENT))


def _content_to_json(content: StoredContent) -> list[int] | dict[str, Any]:
    """Return scene content in the cache's JSON form."""
    if isinstance(content, GradientSceneContent):
        return {
            "level": content.level,
            "points": [list(point) for point in content.points],
            "style": content.style,
        }
    return [content.level, content.x, content.y]


def _content_from_json(data: list[int] | dict[str, Any]) -> StoredContent:
    """Return scene content from the cache's JSON form."""
    if isinstance(data, dict):
        return GradientSceneContent(
            level=data["level"],
            points=tuple((x, y) for x, y in data["points"]),
            style=data["style"],
        )
    return SceneContent(*data)
