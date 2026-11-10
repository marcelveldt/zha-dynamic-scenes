"""Per zone select entity to start a palette."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import DynamicScenesConfigEntry
from .const import DOMAIN, SIGNAL_LIBRARY_UPDATED
from .manager import DynamicScenesManager
from .models import ZoneConfig

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DynamicScenesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a select entity per zone and remove those of deleted zones."""
    manager = entry.runtime_data
    registry = er.async_get(hass)
    zones = {_unique_id(group_id) for group_id in manager.zones}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in zones:
            registry.async_remove(entity.entity_id)
    async_add_entities(ZoneSceneSelect(hass, manager, zone) for zone in manager.zones.values())


class ZoneSceneSelect(SelectEntity, RestoreEntity):
    """
    The palettes of a zone; selecting one starts it.

    The state is the last started palette: lamps do not report a running palette.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "dynamic_scene"
    _attr_should_poll = False

    def __init__(
        self, hass: HomeAssistant, manager: DynamicScenesManager, zone: ZoneConfig
    ) -> None:
        """Initialize the entity on the device of the zone's ZHA group."""
        self._manager = manager
        self._zone = zone
        self._palette_ids: dict[str, str] = {}
        self._attr_options = []
        self._attr_unique_id = _unique_id(zone.group_id)
        self.device_entry = async_entity_id_to_device(hass, zone.entity_id)

    async def async_added_to_hass(self) -> None:
        """Load the zone's palettes and the last started one."""
        await super().async_added_to_hass()
        self._refresh_options()
        if (state := await self.async_get_last_state()) and state.state in self.options:
            self._attr_current_option = state.state
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_LIBRARY_UPDATED, self._handle_library_updated
            )
        )

    async def async_select_option(self, option: str) -> None:
        """Start the selected palette with its own speed and brightness."""
        await self.async_activate(option)

    async def async_activate(
        self,
        palette: str,
        speed: float | None = None,
        brightness: int | None = None,
        transition: float | None = None,
    ) -> None:
        """
        Start a palette on the zone.

        :param palette: Label or name of a palette the zone offers, or a palette id.
        :param speed: Animation speed 0..1 instead of the palette's.
        :param brightness: Brightness 1..255 instead of the palette's.
        :param transition: Transition time in seconds.
        """
        palette_id = self._resolve(palette)
        await self._manager.async_activate(
            self._zone.group_id,
            palette_id,
            speed=speed,
            brightness=brightness,
            transition=transition,
            context=self._context,
        )
        self._attr_current_option = next(
            (label for label, pid in self._palette_ids.items() if pid == palette_id),
            None,
        )
        self.async_write_ha_state()

    async def async_rewrite_scenes(self) -> None:
        """Write the zone's scenes to the lamps again on their next use."""
        self._manager.async_rewrite_zone(self._zone.group_id)

    @callback
    def _handle_library_updated(self) -> None:
        """Show the updated palettes."""
        self._refresh_options()
        self.async_write_ha_state()

    @callback
    def _refresh_options(self) -> None:
        """Read the palettes the zone offers."""
        palettes = self._manager.zone_palettes(self._zone)
        self._palette_ids = {palette.label: palette.palette_id for palette in palettes}
        self._attr_options = list(self._palette_ids)

    def _resolve(self, palette: str) -> str:
        """Return the palette id for a label, name or id."""
        if palette in self._palette_ids:
            return self._palette_ids[palette]
        for candidate in self._manager.zone_palettes(self._zone):
            if candidate.name == palette:
                return candidate.palette_id
        if palette in self._manager.palettes:
            return palette
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_palette",
            translation_placeholders={"palette": palette},
        )


def _unique_id(group_id: int) -> str:
    """Return the unique id of a zone's select entity."""
    return f"{group_id:04x}"
