"""Per zone select entity to start a palette."""

from __future__ import annotations

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN, SelectEntity
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import DynamicScenesConfigEntry
from .const import SIGNAL_LIBRARY_UPDATED, SIGNAL_ZONE_ACTIVATED
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
    zones = {zone.unique_id for zone in manager.zones.values()}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain == SELECT_DOMAIN and entity.unique_id not in zones:
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
        """Initialize the entity on the device of the zone's ZHA group or light."""
        self._manager = manager
        self._zone = zone
        self._palette_ids: dict[str, str] = {}
        self._attr_options = []
        self._attr_unique_id = zone.unique_id
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
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ZONE_ACTIVATED.format(self._zone.zone_id),
                self._handle_activated,
            )
        )

    async def async_select_option(self, option: str) -> None:
        """Start the selected palette with its own speed and brightness."""
        await self._manager.async_activate(
            self._zone.zone_id, self._palette_ids[option], context=self._context
        )

    async def async_rewrite_scenes(self) -> None:
        """Write the zone's scenes to the lamps again on their next use."""
        self._manager.async_rewrite_zone(self._zone.zone_id)

    @callback
    def _handle_activated(self, palette_id: str, context: Context | None) -> None:
        """Show the palette that was started last."""
        if context is not None:
            self.async_set_context(context)
        self._attr_current_option = next(
            (label for label, pid in self._palette_ids.items() if pid == palette_id),
            None,
        )
        self.async_write_ha_state()

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
