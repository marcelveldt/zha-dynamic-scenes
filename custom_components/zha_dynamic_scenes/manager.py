"""Runtime of the integration: palette library, zones and activation."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util.color import color_temperature_mired_to_kelvin

from .const import (
    CONF_ZONES,
    DOMAIN,
    SIGNAL_LIBRARY_UPDATED,
    SIGNAL_ZONE_ACTIVATED,
    STORAGE_VERSION,
)
from .engine import (
    LampTarget,
    SceneCache,
    SceneEngine,
    StoredContent,
    TransportError,
    assign_targets,
)
from .models import Lamp, LampKind, Palette, PaletteSource, ZoneConfig
from .protocol import MAX_LEVEL, GradientSceneContent, SceneContent, level_from_percent
from .zigbee import ZhaSceneTransport, async_group_member_ieees, async_zone_lamps

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 5


class DynamicScenesManager:
    """Palette library, zone configuration and activation of palettes on zones."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: Store[dict[str, Any]],
        data: dict[str, Any],
    ) -> None:
        """
        Initialize the manager.

        :param entry: The config entry; its options hold the zones.
        :param store: Store holding the library and the scene cache.
        :param data: Loaded store data, or an empty dict.
        """
        self.hass = hass
        self.palettes: dict[str, Palette] = {
            item["palette_id"]: Palette.from_dict(item) for item in data.get("palettes", [])
        }
        self.zones: dict[int, ZoneConfig] = {
            zone.group_id: zone
            for zone in map(ZoneConfig.from_dict, entry.options.get(CONF_ZONES, []))
        }
        self._store = store
        self._cache = SceneCache(data, self._schedule_save)
        self._engine = SceneEngine(ZhaSceneTransport(hass), self._cache)

    @classmethod
    async def async_load(cls, hass: HomeAssistant, entry: ConfigEntry) -> DynamicScenesManager:
        """Create the manager with the stored library and scene cache."""
        store = Store[dict[str, Any]](hass, STORAGE_VERSION, DOMAIN)
        return cls(hass, entry, store, await store.async_load() or {})

    @callback
    def zone_palettes(self, zone: ZoneConfig) -> list[Palette]:
        """
        Return the palettes a zone offers, sorted by label.

        Without an explicit choice, a zone offers the imported palettes of the Hue room
        or zone that best matches its lamps, and all custom palettes.
        """
        if zone.palettes is not None:
            palettes = [self.palettes[p] for p in zone.palettes if p in self.palettes]
        else:
            try:
                ieees = async_group_member_ieees(self.hass, zone.group_id)
            except HomeAssistantError as err:
                _LOGGER.warning("Cannot match palettes to zone: %s", err)
                ieees = set()
            palettes = default_palettes(self.palettes.values(), ieees)
        return sorted(palettes, key=lambda palette: palette.label.casefold())

    async def async_activate(
        self,
        group_id: int,
        palette_id: str,
        *,
        speed: float | None = None,
        brightness: int | None = None,
        transition: float | None = None,
        context: Context | None = None,
    ) -> None:
        """
        Start a palette on a zone.

        Dynamic lamps animate the palette; the other lights get a static colour or
        colour temperature from it. Then sends `SIGNAL_ZONE_ACTIVATED` for the zone.

        :param speed: Animation speed 0..1 instead of the palette's.
        :param brightness: Brightness 1..255 for all lamps instead of the palette's.
        :param transition: Transition time in seconds.
        :raises HomeAssistantError: The zone's group is unknown or could not be reached.
        """
        palette = self.palettes[palette_id]
        lamps = async_zone_lamps(self.hass, group_id)
        targets = assign_targets(lamps, self.zones[group_id].clusters, palette)
        dynamic = [lamp for lamp in lamps if lamp.kind is LampKind.DYNAMIC]
        gradient_lamps: set[str] = set()
        failed: list[Lamp] = []
        if dynamic:
            gradient_lamps = await self._engine.async_gradient_lamps(dynamic)
            contents = [
                (lamp, _scene_content(targets[lamp.ieee], lamp.ieee in gradient_lamps))
                for lamp in dynamic
            ]
            try:
                failed = await self._engine.async_start(
                    group_id,
                    palette,
                    contents,
                    speed=palette.speed if speed is None else speed,
                    brightness=None if brightness is None else min(MAX_LEVEL, brightness),
                    transition=None if transition is None else round(transition * 10),
                )
            except TransportError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="send_failed",
                    translation_placeholders={"error": str(err)},
                ) from err
        # lamps that could not store the scene still get their start colour, gradient lamps
        # the first point of their gradient
        for lamp in failed:
            if lamp.ieee in gradient_lamps:
                target = targets[lamp.ieee]
                targets[lamp.ieee] = replace(target, xy=target.gradient.points[0])
        static = [lamp for lamp in lamps if lamp.kind is not LampKind.DYNAMIC] + failed
        results = await asyncio.gather(
            *(
                self._async_turn_on(lamp, targets[lamp.ieee], brightness, transition, context)
                for lamp in static
            ),
            return_exceptions=True,
        )
        for lamp, result in zip(static, results, strict=True):
            if isinstance(result, Exception):
                _LOGGER.warning("Could not set %s to its start state: %s", lamp.entity_id, result)
        async_dispatcher_send(self.hass, SIGNAL_ZONE_ACTIVATED.format(group_id), palette_id)

    @callback
    def async_update_palettes(self, palettes: Iterable[Palette]) -> None:
        """
        Add palettes to the library or replace those with the same id.

        A replaced palette keeps the start states of lamps the new one does not list: a
        lamp that moved from the Hue bridge to ZHA is gone from a later bridge import.
        """
        for palette in palettes:
            if old := self.palettes.get(palette.palette_id):
                palette.actions = {**old.actions, **palette.actions}
                palette.group_name = palette.group_name or old.group_name
            self.palettes[palette.palette_id] = palette
        self._library_updated()

    @callback
    def async_remove_palettes(self, palette_ids: Iterable[str]) -> None:
        """Remove palettes from the library."""
        for palette_id in palette_ids:
            self.palettes.pop(palette_id, None)
        self._library_updated()

    @callback
    def async_rewrite_zone(self, group_id: int) -> None:
        """Write the zone's scenes to the lamps again on the next activation of each palette."""
        self._cache.forget_zone(group_id)

    @callback
    def forget_lamp(self, ieee: str) -> None:
        """Forget the scenes stored in a lamp, so they are written again when used."""
        self._cache.forget_lamp(ieee)

    async def _async_turn_on(
        self,
        lamp: Lamp,
        target: LampTarget,
        brightness: int | None,
        transition: float | None,
        context: Context | None,
    ) -> None:
        """Set a lamp that does not animate to its start state."""
        data: dict[str, Any] = {
            ATTR_ENTITY_ID: lamp.entity_id,
            ATTR_BRIGHTNESS: brightness or level_from_percent(target.brightness),
        }
        if lamp.kind in (LampKind.DYNAMIC, LampKind.COLOR):
            data[ATTR_XY_COLOR] = target.xy
        elif lamp.kind is LampKind.COLOR_TEMP:
            if target.mirek:
                data[ATTR_COLOR_TEMP_KELVIN] = color_temperature_mired_to_kelvin(target.mirek)
            else:
                data[ATTR_XY_COLOR] = target.xy
        if transition is not None:
            data[ATTR_TRANSITION] = transition
        await self.hass.services.async_call(
            LIGHT_DOMAIN, SERVICE_TURN_ON, data, blocking=True, context=context
        )

    async def async_save(self) -> None:
        """Save the library and the scene cache now."""
        await self._store.async_save(self._data_to_save())

    @callback
    def _library_updated(self) -> None:
        """Persist the library and tell the zones."""
        self._schedule_save()
        async_dispatcher_send(self.hass, SIGNAL_LIBRARY_UPDATED)

    @callback
    def _schedule_save(self) -> None:
        """Save the library and the scene cache soon."""
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        """Return the library and the scene cache to store."""
        return {
            "palettes": [palette.to_dict() for palette in self.palettes.values()],
            **self._cache.as_dict(),
        }


def default_palettes(palettes: Iterable[Palette], ieees: set[str]) -> list[Palette]:
    """
    Return the palettes a zone with these lamps offers by default.

    That is all custom palettes, plus the imported palettes of the Hue room or zone whose
    lamps overlap best (Jaccard index) with the given lamps.
    """
    palettes = list(palettes)
    hue_groups: defaultdict[str | None, set[str]] = defaultdict(set)
    for palette in palettes:
        if palette.source is PaletteSource.HUE:
            hue_groups[palette.group_name].update(palette.actions)
    scores = {
        group: len(members & ieees) / len(members | ieees)
        for group, members in hue_groups.items()
        if members & ieees
    }
    best = max(scores.values(), default=None)
    return [
        palette
        for palette in palettes
        if palette.source is PaletteSource.CUSTOM
        or (best is not None and scores.get(palette.group_name) == best)
    ]


def _scene_content(target: LampTarget, gradient: bool) -> StoredContent:
    """Return what a dynamic lamp stores for its start state."""
    if gradient:
        return GradientSceneContent.from_gradient(
            target.gradient.points, target.gradient.mode, target.brightness
        )
    return SceneContent.from_xy(target.xy, target.brightness)
