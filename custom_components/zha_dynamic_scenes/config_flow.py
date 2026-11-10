"""Config and options flow for ZHA dynamic scenes."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.color import color_RGB_to_xy, color_xy_to_RGB
from homeassistant.util.ulid import ulid_now
import probatio

from .const import (
    CONF_HUE_APP_KEY,
    CONF_HUE_HOST,
    CONF_ZONES,
    DEFAULT_SPEED,
    DOMAIN,
    MIN_PALETTE_COLORS,
)
from .hue_import import HueAuthError, HueImportError, async_fetch_palettes
from .models import Palette, PaletteColor, PaletteSource, ZoneConfig
from .protocol import MAX_PALETTE_COLORS
from .zigbee import async_group_lights, async_group_name, async_zha_ready, async_zone_lamps

if TYPE_CHECKING:
    from .manager import DynamicScenesManager

CONF_AUTOMATIC = "automatic"
CONF_BRIGHTNESS = "brightness"
CONF_CLUSTERS = "clusters"
CONF_COLORS = "colors"
CONF_DELETE = "delete"
CONF_GROUP = "group"
CONF_LIGHTS = "lights"
CONF_NAME = "name"
CONF_PALETTE = "palette"
CONF_PALETTES = "palettes"
CONF_SPEED = "speed"
CONF_ZONE = "zone"

DEFAULT_BRIGHTNESS = 80
MIN_CLUSTER_LIGHTS = 2

_HEX_COLOR = re.compile(r"#?([0-9a-fA-F]{6})")

HUE_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HUE_HOST): TextSelector(),
        probatio.Required(CONF_HUE_APP_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)
PALETTE_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_NAME): TextSelector(),
        probatio.Required(CONF_COLORS): TextSelector(),
        probatio.Required(CONF_SPEED): NumberSelector(
            NumberSelectorConfig(min=0, max=1, step=0.01, mode=NumberSelectorMode.SLIDER)
        ),
        probatio.Required(CONF_BRIGHTNESS): NumberSelector(
            NumberSelectorConfig(
                min=1, max=100, unit_of_measurement="%", mode=NumberSelectorMode.SLIDER
            )
        ),
    }
)


class DynamicScenesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the integration; zones and palettes are managed in the options."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm the setup."""
        if not async_zha_ready(self.hass):
            return self.async_abort(reason="zha_not_ready")
        if user_input is not None:
            return self.async_create_entry(
                title="ZHA dynamic scenes", data={}, options={CONF_ZONES: []}
            )
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return DynamicScenesOptionsFlow()


class DynamicScenesOptionsFlow(OptionsFlowWithReload):
    """Import and edit palettes, configure zones and their clusters."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._options: dict[str, Any] = {}
        self._zones: dict[int, ZoneConfig] = {}
        self._zone: ZoneConfig | None = None
        self._palette_id: str | None = None
        self._imported = 0

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show what can be changed."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        self._options = dict(self.config_entry.options)
        self._zones = {
            zone.group_id: zone
            for zone in map(ZoneConfig.from_dict, self._options.get(CONF_ZONES, []))
        }
        palettes = self._manager.palettes.values()
        menu = ["import_hue", "add_palette"]
        if any(palette.source is PaletteSource.CUSTOM for palette in palettes):
            menu.append("edit_palette")
        if palettes:
            menu.append("remove_palettes")
        menu.append("add_zone")
        if self._zones:
            menu.append("edit_zone")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_import_hue(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Import the dynamic scene palettes of a Hue bridge."""
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass, verify_ssl=False)
            try:
                palettes = await async_fetch_palettes(
                    session, user_input[CONF_HUE_HOST], user_input[CONF_HUE_APP_KEY]
                )
            except HueAuthError:
                errors["base"] = "invalid_auth"
            except HueImportError:
                errors["base"] = "cannot_connect"
            else:
                self._manager.async_update_palettes(palettes)
                self._options.update(user_input)
                self._imported = len(palettes)
                return await self.async_step_import_done()
        return self.async_show_form(
            step_id="import_hue",
            data_schema=self.add_suggested_values_to_schema(
                HUE_SCHEMA, user_input or self._options
            ),
            errors=errors,
        )

    async def async_step_import_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Report the import."""
        if user_input is not None:
            return self._async_save()
        return self.async_show_form(
            step_id="import_done",
            description_placeholders={"count": str(self._imported)},
        )

    async def async_step_add_palette(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a custom palette."""
        return self._async_palette_form("add_palette", None, user_input)

    async def async_step_edit_palette(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the custom palette to edit."""
        if user_input is not None:
            self._palette_id = user_input[CONF_PALETTE]
            return await self.async_step_palette()
        custom = [
            palette
            for palette in self._manager.palettes.values()
            if palette.source is PaletteSource.CUSTOM
        ]
        return self.async_show_form(
            step_id="edit_palette",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_PALETTE): _palette_selector(custom)}
            ),
        )

    async def async_step_palette(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit or delete a custom palette."""
        assert self._palette_id is not None
        return self._async_palette_form(
            "palette", self._manager.palettes[self._palette_id], user_input
        )

    async def async_step_remove_palettes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove palettes from the library."""
        if user_input is not None:
            self._manager.async_remove_palettes(user_input[CONF_PALETTES])
            return self._async_save()
        return self.async_show_form(
            step_id="remove_palettes",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_PALETTES): _palette_selector(
                        self._manager.palettes.values(), multiple=True
                    )
                }
            ),
        )

    async def async_step_add_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a zone by picking the light entity of a ZHA group."""
        lights = {
            entity_id: group_id
            for entity_id, group_id in async_group_lights(self.hass).items()
            if group_id not in self._zones
        }
        if not lights:
            return self.async_abort(reason="no_groups")
        if user_input is not None:
            entity_id = user_input[CONF_GROUP]
            entry = er.async_get(self.hass).async_get(entity_id)
            assert entry is not None  # ZHA group lights have a unique id
            self._zone = ZoneConfig(group_id=lights[entity_id], entity_id=entry.id)
            self._zones[self._zone.group_id] = self._zone
            return await self.async_step_zone()
        return self.async_show_form(
            step_id="add_zone",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_GROUP): EntitySelector(
                        EntitySelectorConfig(include_entities=list(lights))
                    )
                }
            ),
        )

    async def async_step_edit_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the zone to edit."""
        if user_input is not None:
            self._zone = self._zones[int(user_input[CONF_ZONE])]
            return await self.async_step_zone()
        options = [
            SelectOptionDict(value=str(group_id), label=self._zone_name(group_id))
            for group_id in self._zones
        ]
        return self.async_show_form(
            step_id="edit_zone",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_ZONE): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show what can be changed in a zone."""
        zone = self._current_zone
        names = self._lamp_names(zone)
        clusters = [
            ", ".join(names.get(ieee, ieee) for ieee in cluster) for cluster in zone.clusters
        ]
        menu = ["zone_palettes", "add_cluster"]
        if zone.clusters:
            menu.append("remove_clusters")
        menu += ["delete_zone", "save"]
        return self.async_show_menu(
            step_id="zone",
            menu_options=menu,
            description_placeholders={
                "zone": self._zone_name(zone.group_id),
                "clusters": "\n".join(f"- {cluster}" for cluster in clusters) or "-",
            },
        )

    async def async_step_zone_palettes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the palettes a zone offers."""
        zone = self._current_zone
        if user_input is not None:
            zone.palettes = None if user_input[CONF_AUTOMATIC] else user_input[CONF_PALETTES]
            return await self.async_step_zone()
        offered = [palette.palette_id for palette in self._manager.zone_palettes(zone)]
        return self.async_show_form(
            step_id="zone_palettes",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_AUTOMATIC, default=zone.palettes is None): (
                        BooleanSelector()
                    ),
                    probatio.Optional(CONF_PALETTES, default=offered): _palette_selector(
                        self._manager.palettes.values(), multiple=True
                    ),
                }
            ),
        )

    async def async_step_add_cluster(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a cluster: lamps that start with the same colour."""
        zone = self._current_zone
        clustered = {ieee for cluster in zone.clusters for ieee in cluster}
        ieee_by_entity = {
            lamp.entity_id: lamp.ieee
            for lamp in async_zone_lamps(self.hass, zone.group_id)
            if lamp.ieee not in clustered
        }
        errors: dict[str, str] = {}
        if user_input is not None:
            if len(user_input[CONF_LIGHTS]) < MIN_CLUSTER_LIGHTS:
                errors[CONF_LIGHTS] = "cluster_too_small"
            else:
                zone.clusters.append(
                    [ieee_by_entity[entity_id] for entity_id in user_input[CONF_LIGHTS]]
                )
                return await self.async_step_zone()
        return self.async_show_form(
            step_id="add_cluster",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_LIGHTS): EntitySelector(
                        EntitySelectorConfig(include_entities=list(ieee_by_entity), multiple=True)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_remove_clusters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove clusters from a zone."""
        zone = self._current_zone
        if user_input is not None:
            removed = {int(index) for index in user_input[CONF_CLUSTERS]}
            zone.clusters = [c for i, c in enumerate(zone.clusters) if i not in removed]
            return await self.async_step_zone()
        names = self._lamp_names(zone)
        options = [
            SelectOptionDict(
                value=str(index),
                label=", ".join(names.get(ieee, ieee) for ieee in cluster),
            )
            for index, cluster in enumerate(zone.clusters)
        ]
        return self.async_show_form(
            step_id="remove_clusters",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_CLUSTERS): SelectSelector(
                        SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
        )

    async def async_step_delete_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Delete the zone and save."""
        del self._zones[self._current_zone.group_id]
        return self._async_save()

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Save the zone."""
        return self._async_save()

    @property
    def _manager(self) -> DynamicScenesManager:
        """Return the running manager."""
        manager: DynamicScenesManager = self.config_entry.runtime_data
        return manager

    @property
    def _current_zone(self) -> ZoneConfig:
        """Return the zone being edited."""
        assert self._zone is not None
        return self._zone

    @callback
    def _async_palette_form(
        self, step_id: str, palette: Palette | None, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Show and handle the form of a custom palette."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if palette is not None and user_input.get(CONF_DELETE):
                self._manager.async_remove_palettes([palette.palette_id])
                return self._async_save()
            try:
                colors = _parse_colors(user_input[CONF_COLORS])
            except ValueError:
                errors[CONF_COLORS] = "invalid_colors"
            else:
                self._manager.async_update_palettes(
                    [
                        Palette(
                            palette_id=palette.palette_id if palette else f"custom:{ulid_now()}",
                            name=user_input[CONF_NAME],
                            source=PaletteSource.CUSTOM,
                            colors=[
                                PaletteColor(x, y, user_input[CONF_BRIGHTNESS]) for x, y in colors
                            ],
                            speed=user_input[CONF_SPEED],
                        )
                    ]
                )
                return self._async_save()
        schema = PALETTE_SCHEMA
        values: dict[str, Any] = {
            CONF_SPEED: DEFAULT_SPEED,
            CONF_BRIGHTNESS: DEFAULT_BRIGHTNESS,
        }
        if palette is not None:
            schema = schema.extend({probatio.Optional(CONF_DELETE): BooleanSelector()})
            values = {
                CONF_NAME: palette.name,
                CONF_COLORS: ", ".join(_hex(color) for color in palette.colors),
                CONF_SPEED: palette.speed,
                CONF_BRIGHTNESS: palette.colors[0].brightness,
            }
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, user_input or values),
            errors=errors,
        )

    @callback
    def _async_save(self) -> ConfigFlowResult:
        """Save the options; a changed zone setup reloads the integration."""
        return self.async_create_entry(
            data={**self._options, CONF_ZONES: [z.to_dict() for z in self._zones.values()]}
        )

    @callback
    def _zone_name(self, group_id: int) -> str:
        """Return the ZHA group name of a zone."""
        return async_group_name(self.hass, group_id) or f"0x{group_id:04x}"

    @callback
    def _lamp_names(self, zone: ZoneConfig) -> dict[str, str]:
        """Return the names of a zone's lamps by IEEE."""
        return {
            lamp.ieee: state.name
            if (state := self.hass.states.get(lamp.entity_id))
            else lamp.entity_id
            for lamp in async_zone_lamps(self.hass, zone.group_id)
        }


def _palette_selector(palettes: Iterable[Palette], *, multiple: bool = False) -> SelectSelector:
    """Return a selector of palettes by label."""
    options = [
        SelectOptionDict(value=palette.palette_id, label=palette.label)
        for palette in sorted(palettes, key=lambda palette: palette.label.casefold())
    ]
    return SelectSelector(SelectSelectorConfig(options=options, multiple=multiple))


def _parse_colors(text: str) -> list[tuple[float, float]]:
    """
    Return the CIE xy of a list of hex RGB colours.

    :param text: 2..9 colours like `#ff8800`, separated by commas or spaces.
    :raises ValueError: The text is not such a list.
    """
    tokens = [token for token in re.split(r"[,\s]+", text) if token]
    matches = [_HEX_COLOR.fullmatch(token) for token in tokens]
    if not MIN_PALETTE_COLORS <= len(tokens) <= MAX_PALETTE_COLORS or not all(matches):
        raise ValueError(text)
    return [
        color_RGB_to_xy(*bytes.fromhex(match.group(1))) for match in matches if match is not None
    ]


def _hex(color: PaletteColor) -> str:
    """Return a palette colour as hex RGB."""
    return "#{:02x}{:02x}{:02x}".format(*color_xy_to_RGB(color.x, color.y))
