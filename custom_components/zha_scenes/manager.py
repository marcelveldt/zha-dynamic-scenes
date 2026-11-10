"""Runtime of the integration: palette library, zones and activation of their scenes."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from functools import partial
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

from .builtin import STANDARD_SCENES, async_builtin_palettes, async_scene_names
from .const import (
    CONF_CORRECTIONS,
    CONF_ZONES,
    DOMAIN,
    SIGNAL_LIBRARY_UPDATED,
    SIGNAL_ZONE_ACTIVATED,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)
from .engine import (
    SCENE_GROUP,
    LampTarget,
    PaletteContent,
    SceneCache,
    SceneEngine,
    TransportError,
    assign_targets,
    corrected_mireds,
    scene_key,
)
from .models import Lamp, LampKind, Palette, PaletteSource, StandardScene, ZoneConfig
from .protocol import (
    MAX_LEVEL,
    ColorTemperatureSceneContent,
    GradientSceneContent,
    SceneContent,
    level_from_percent,
)
from .zigbee import ZhaSceneTransport, async_zone_lamps

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 5
STANDARD_SCENE_IDS = [scene.scene_id for scene in STANDARD_SCENES.values()]


class ZhaScenesStore(Store[dict[str, Any]]):
    """Storage of the custom palettes and the scene cache."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Key the scene contents of version 1.1, all in group 0x0000, by group and scene."""
        if old_minor_version == 1:
            old_data["scenes"] = {
                ieee: {
                    scene_key(SCENE_GROUP, int(scene_id)): content
                    for scene_id, content in scenes.items()
                }
                for ieee, scenes in old_data.get("scenes", {}).items()
            }
        return old_data


class ZhaScenesManager:
    """Palette library, zone configuration and activation of scenes on zones."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: Store[dict[str, Any]],
        data: dict[str, Any],
        *,
        builtin: dict[str, Palette],
        names: dict[str, str],
    ) -> None:
        """
        Initialize the manager.

        :param entry: The config entry; its options hold the zones and lamp corrections.
        :param store: Store holding the custom palettes and the scene cache.
        :param data: Loaded store data, or an empty dict.
        :param builtin: The built-in palettes by id.
        :param names: Names of the built-in palettes and standard scenes by id, in Home
            Assistant's language.
        """
        self.hass = hass
        self.palettes: dict[str, Palette] = {
            **builtin,
            **{item["palette_id"]: Palette.from_dict(item) for item in data.get("palettes", [])},
        }
        self.zones: dict[str, ZoneConfig] = {
            zone.zone_id: zone
            for zone in map(ZoneConfig.from_dict, entry.options.get(CONF_ZONES, []))
        }
        self._corrections: dict[str, int] = entry.options.get(CONF_CORRECTIONS, {})
        self._names = names
        self._store = store
        self._cache = SceneCache(data, self._schedule_save)
        self._engine = SceneEngine(ZhaSceneTransport(hass), self._cache)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @classmethod
    async def async_load(cls, hass: HomeAssistant, entry: ConfigEntry) -> ZhaScenesManager:
        """Create the manager with the palette library and the scene cache."""
        store = ZhaScenesStore(hass, STORAGE_VERSION, DOMAIN, minor_version=STORAGE_MINOR_VERSION)
        return cls(
            hass,
            entry,
            store,
            await store.async_load() or {},
            builtin=await async_builtin_palettes(hass),
            names=await async_scene_names(hass, hass.config.language),
        )

    @callback
    def standard_scene_names(self) -> dict[str, str]:
        """Return the names of the standard scenes by key, in Home Assistant's language."""
        return {key: self._names.get(key, key) for key in STANDARD_SCENES}

    @callback
    def palette_name(self, palette: Palette) -> str:
        """Return the name to show for a palette, translated for a built-in one."""
        return self._names.get(palette.palette_id, palette.name)

    @callback
    def zone_palettes(self, zone: ZoneConfig) -> list[Palette]:
        """Return the palettes a zone offers, sorted by name."""
        palettes = [self.palettes[p] for p in zone.palettes if p in self.palettes]
        return sorted(palettes, key=lambda palette: self.palette_name(palette).casefold())

    async def async_activate(
        self,
        zone_id: str,
        key: str,
        *,
        speed: float | None = None,
        brightness: int | None = None,
        transition: float | None = None,
        context: Context | None = None,
    ) -> None:
        """
        Start a standard scene or a palette on a zone.

        The lamps recall a standard scene; it is stored in lamps that do not store it yet.
        Dynamic lamps animate a palette; the other lights get a static colour or colour
        temperature from it. Then sends `SIGNAL_ZONE_ACTIVATED` for the zone.

        :param zone_id: `ZoneConfig.zone_id` of the zone.
        :param key: Key of a standard scene or id of a palette.
        :param speed: Animation speed 0..1 instead of the palette's; not used by a standard
            scene.
        :param brightness: Brightness 1..255 for all lamps instead of the scene's.
        :param transition: Transition time in seconds.
        :raises HomeAssistantError: The zone's group or light is unknown or could not be
            reached.
        """
        # one activation per zone at a time, so the lights and the zone's signal end up with
        # the start that came last
        async with self._locks[zone_id]:
            zone = self.zones[zone_id]
            lamps = async_zone_lamps(self.hass, zone)
            try:
                if (scene := STANDARD_SCENES.get(key)) is not None:
                    await self._engine.async_recall(
                        zone,
                        scene.scene_id,
                        [(lamp, self._standard_content(lamp, scene)) for lamp in lamps],
                        partial(self._async_set_lamp, context=context),
                        brightness=None if brightness is None else min(MAX_LEVEL, brightness),
                        transition=None if transition is None else round(transition * 10),
                    )
                else:
                    await self._async_start(
                        zone,
                        lamps,
                        self.palettes[key],
                        speed=speed,
                        brightness=brightness,
                        transition=transition,
                        context=context,
                    )
            except TransportError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="send_failed",
                    translation_placeholders={"error": str(err)},
                ) from err
            async_dispatcher_send(self.hass, SIGNAL_ZONE_ACTIVATED.format(zone_id), key, context)

    @callback
    def async_update_palettes(self, palettes: Iterable[Palette]) -> None:
        """Add custom palettes to the library or replace those with the same id."""
        for palette in palettes:
            self.palettes[palette.palette_id] = palette
        self._library_updated()

    @callback
    def async_remove_palettes(self, palette_ids: Iterable[str]) -> None:
        """Remove custom palettes from the library."""
        for palette_id in palette_ids:
            self.palettes.pop(palette_id, None)
        self._library_updated()

    @callback
    def async_rewrite_zone(self, zone_id: str) -> None:
        """Write the zone's scenes to the lamps again on the next activation of each."""
        zone = self.zones[zone_id]
        self._cache.forget_zone(zone_id)
        self._cache.forget_scenes(
            SCENE_GROUP if zone.group_id is None else zone.group_id, STANDARD_SCENE_IDS, zone.ieee
        )

    @callback
    def forget_lamp(self, ieee: str) -> None:
        """Forget the scenes stored in a lamp, so they are written again when used."""
        self._cache.forget_lamp(ieee)

    @callback
    def forget_group(self, group_id: int) -> None:
        """Forget the standard scenes stored under a group, so they are written again when used."""
        self._cache.forget_scenes(group_id, STANDARD_SCENE_IDS)

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
        """Return the custom palettes and the scene cache to store."""
        return {
            "palettes": [
                palette.to_dict()
                for palette in self.palettes.values()
                if palette.source is PaletteSource.CUSTOM
            ],
            **self._cache.as_dict(),
        }

    async def _async_start(
        self,
        zone: ZoneConfig,
        lamps: list[Lamp],
        palette: Palette,
        *,
        speed: float | None,
        brightness: int | None,
        transition: float | None,
        context: Context | None,
    ) -> None:
        """
        Start a palette on the lamps of a zone.

        :raises TransportError: The start could not be sent.
        """
        targets = assign_targets(lamps, zone.clusters, palette)
        dynamic = [lamp for lamp in lamps if lamp.kind is LampKind.DYNAMIC]
        gradient_lamps: set[str] = set()
        failed: list[Lamp] = []
        if dynamic:
            gradient_lamps = await self._engine.async_gradient_lamps(dynamic)
            contents = [
                (lamp, _scene_content(targets[lamp.ieee], lamp.ieee in gradient_lamps))
                for lamp in dynamic
            ]
            failed = await self._engine.async_start(
                zone,
                palette,
                contents,
                speed=palette.speed if speed is None else speed,
                brightness=None if brightness is None else min(MAX_LEVEL, brightness),
                transition=None if transition is None else round(transition * 10),
            )
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

    async def _async_set_lamp(
        self,
        lamp: Lamp,
        content: ColorTemperatureSceneContent,
        transition: int,
        *,
        context: Context | None,
    ) -> bool:
        """Set a lamp to a standard scene state through Home Assistant; return whether it worked."""
        data: dict[str, Any] = {
            ATTR_ENTITY_ID: lamp.entity_id,
            ATTR_BRIGHTNESS: content.level,
            ATTR_TRANSITION: transition / 10,
        }
        if content.mireds is not None:
            data[ATTR_COLOR_TEMP_KELVIN] = color_temperature_mired_to_kelvin(content.mireds)
        try:
            await self.hass.services.async_call(
                LIGHT_DOMAIN, SERVICE_TURN_ON, data, blocking=True, context=context
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Could not set %s to the scene: %s", lamp.entity_id, err)
            return False
        return True

    @callback
    def _standard_content(self, lamp: Lamp, scene: StandardScene) -> ColorTemperatureSceneContent:
        """Return what a lamp stores for a standard scene, with the lamp's correction applied."""
        mireds = None
        if lamp.max_mireds is not None:
            mireds = scene.mireds
            if (kelvin := self._corrections.get(lamp.ieee)) is not None:
                mireds = corrected_mireds(mireds, lamp.max_mireds, 1_000_000 / kelvin)
        return ColorTemperatureSceneContent(level_from_percent(scene.brightness), mireds)


def _scene_content(target: LampTarget, gradient: bool) -> PaletteContent:
    """Return what a dynamic lamp stores for its start state."""
    if gradient:
        return GradientSceneContent.from_gradient(
            target.gradient.points, target.gradient.mode, target.brightness
        )
    return SceneContent.from_xy(target.xy, target.brightness)
