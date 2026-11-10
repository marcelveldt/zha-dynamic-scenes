"""Scene entities: one per palette a zone offers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.components.scene import ATTR_TRANSITION, DOMAIN as SCENE_DOMAIN, BaseScene
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DynamicScenesConfigEntry
from .const import ATTR_SPEED, SIGNAL_LIBRARY_UPDATED, SIGNAL_ZONE_ACTIVATED
from .manager import DynamicScenesManager
from .models import Palette, ZoneConfig

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DynamicScenesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a scene per palette a zone offers and keep them in line with the library."""
    manager = entry.runtime_data
    registry = er.async_get(hass)
    entities: dict[str, ZonePaletteScene] = {}

    @callback
    def async_sync_entities() -> None:
        """Add scenes for newly offered palettes and remove those no longer offered."""
        offered = {
            _unique_id(zone, palette): (zone, palette)
            for zone in manager.zones.values()
            for palette in manager.zone_palettes(zone)
        }
        for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity_entry.domain == SCENE_DOMAIN and entity_entry.unique_id not in offered:
                # also removes the entity from Home Assistant
                registry.async_remove(entity_entry.entity_id)
        for unique_id in entities.keys() - offered.keys():
            del entities[unique_id]
        new = {
            unique_id: ZonePaletteScene(hass, manager, zone, palette)
            for unique_id, (zone, palette) in offered.items()
            if unique_id not in entities
        }
        entities.update(new)
        async_add_entities(new.values())

    async_sync_entities()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_LIBRARY_UPDATED, async_sync_entities)
    )


class ZonePaletteScene(BaseScene):
    """
    A palette of a zone; activating it starts the palette on the zone.

    The state is the time the palette last started on the zone, also when the zone's
    select started it.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        manager: DynamicScenesManager,
        zone: ZoneConfig,
        palette: Palette,
    ) -> None:
        """Initialize the scene on the device of the zone's ZHA group or light."""
        self._manager = manager
        self._zone_id = zone.zone_id
        self._palette_id = palette.palette_id
        self._attr_name = palette.name
        self._attr_unique_id = _unique_id(zone, palette)
        self.device_entry = async_entity_id_to_device(hass, zone.entity_id)

    async def async_added_to_hass(self) -> None:
        """Follow renames of the palette and starts of the zone."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_LIBRARY_UPDATED, self._handle_library_updated
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_ZONE_ACTIVATED.format(self._zone_id), self._handle_activated
            )
        )

    async def _async_activate(self, **kwargs: Any) -> None:  # noqa: ANN401
        """
        Start the palette on the zone.

        :param kwargs: `transition` in seconds, and from the `activate` service `speed`
            (0..1) and `brightness` (1..255) instead of the palette's.
        """
        await self._manager.async_activate(
            self._zone_id,
            self._palette_id,
            speed=kwargs.get(ATTR_SPEED),
            brightness=kwargs.get(ATTR_BRIGHTNESS),
            transition=kwargs.get(ATTR_TRANSITION),
            context=self._context,
        )

    @callback
    def _handle_activated(self, palette_id: str, context: Context | None) -> None:
        """Record a successful start of this palette, whichever entity started it."""
        if palette_id == self._palette_id:
            if context is not None:
                self.async_set_context(context)
            self._async_record_activation()
            self.async_write_ha_state()

    @callback
    def _handle_library_updated(self) -> None:
        """Show the palette's current name."""
        # a removed palette's scene is being removed by the platform
        if palette := self._manager.palettes.get(self._palette_id):
            self._attr_name = palette.name
            self.async_write_ha_state()


def _unique_id(zone: ZoneConfig, palette: Palette) -> str:
    """Return the unique id of a zone's palette scene."""
    return f"{zone.unique_id}_{palette.palette_id}"
