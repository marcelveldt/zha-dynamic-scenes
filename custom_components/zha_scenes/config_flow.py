"""Config and options flow for ZHA Scenes."""

from __future__ import annotations

from collections.abc import Iterable
import math
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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
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
)
from homeassistant.util.color import color_RGB_to_xy, color_xy_to_RGB
from homeassistant.util.ulid import ulid_now
import probatio

from .const import CONF_CORRECTIONS, CONF_ZONES, DEFAULT_SPEED, DOMAIN, MIN_PALETTE_COLORS
from .engine import CORRECTION_PIVOT
from .models import Lamp, Palette, PaletteColor, PaletteSource, ZoneConfig
from .protocol import MAX_PALETTE_COLORS
from .zigbee import (
    async_color_temperature_lights,
    async_device_lights,
    async_group_lights,
    async_group_name,
    async_zha_ready,
    async_zone_lamps,
)

if TYPE_CHECKING:
    from .manager import ZhaScenesManager

CONF_BRIGHTNESS = "brightness"
CONF_CLUSTERS = "clusters"
CONF_COLORS = "colors"
CONF_DELETE = "delete"
CONF_LIGHT = "light"
CONF_LIGHTS = "lights"
CONF_NAME = "name"
CONF_PALETTE = "palette"
CONF_PALETTES = "palettes"
CONF_SPEED = "speed"
CONF_WARMEST_WHITE = "warmest_white"
CONF_ZONE = "zone"

DEFAULT_BRIGHTNESS = 80
MIN_CLUSTER_LIGHTS = 2

_HEX_COLOR = re.compile(r"#?([0-9a-fA-F]{6})")

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


class ZhaScenesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the integration; zones and palettes are managed in the options."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm the setup."""
        if not async_zha_ready(self.hass):
            return self.async_abort(reason="zha_not_ready")
        if user_input is not None:
            return self.async_create_entry(title="ZHA Scenes", data={}, options={CONF_ZONES: []})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return ZhaScenesOptionsFlow()


class ZhaScenesOptionsFlow(OptionsFlowWithReload):
    """Edit custom palettes, configure zones and their clusters, and correct lamps."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._options: dict[str, Any] = {}
        self._zones: dict[str, ZoneConfig] = {}
        self._zone: ZoneConfig | None = None
        self._palette_id: str | None = None
        self._lamp: Lamp | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show what can be changed."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        self._options = dict(self.config_entry.options)
        self._zones = {
            zone.zone_id: zone
            for zone in map(ZoneConfig.from_dict, self._options.get(CONF_ZONES, []))
        }
        menu = ["add_palette"]
        if self._custom_palettes:
            menu += ["edit_palette", "remove_palettes"]
        menu.append("add_zone")
        if self._zones:
            menu.append("edit_zone")
        menu.append("lamp_correction")
        return self.async_show_menu(step_id="init", menu_options=menu)

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
        return self.async_show_form(
            step_id="edit_palette",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_PALETTE): self._palette_selector(self._custom_palettes)}
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
        """Remove custom palettes from the library."""
        if user_input is not None:
            self._manager.async_remove_palettes(user_input[CONF_PALETTES])
            return self._async_save()
        return self.async_show_form(
            step_id="remove_palettes",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_PALETTES): self._palette_selector(
                        self._custom_palettes, multiple=True
                    )
                }
            ),
        )

    async def async_step_add_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a zone by picking the light entity of a ZHA group or a single ZHA light."""
        zones = self._zones.values()
        zoned = {zone.group_id for zone in zones} | {zone.ieee for zone in zones}
        groups = {
            entity_id: group_id
            for entity_id, group_id in async_group_lights(self.hass).items()
            if group_id not in zoned
        }
        lamps = {
            entity_id: ieee
            for entity_id, ieee in async_device_lights(self.hass).items()
            if ieee not in zoned
        }
        if not groups and not lamps:
            return self.async_abort(reason="no_lights")
        if user_input is not None:
            entity_id = user_input[CONF_LIGHT]
            entry = er.async_get(self.hass).async_get(entity_id)
            assert entry is not None  # ZHA lights have a unique id
            self._zone = ZoneConfig(
                group_id=groups.get(entity_id), entity_id=entry.id, ieee=lamps.get(entity_id)
            )
            self._zones[self._zone.zone_id] = self._zone
            return await self.async_step_zone_palettes()
        return self.async_show_form(
            step_id="add_zone",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_LIGHT): EntitySelector(
                        EntitySelectorConfig(include_entities=[*groups, *lamps])
                    )
                }
            ),
        )

    async def async_step_edit_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the zone to edit."""
        if user_input is not None:
            self._zone = self._zones[user_input[CONF_ZONE]]
            return await self.async_step_zone()
        options = [
            SelectOptionDict(value=zone_id, label=self._zone_name(zone))
            for zone_id, zone in self._zones.items()
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
        menu = ["zone_palettes"]
        if zone.group_id is not None:  # a single light has nothing to cluster
            menu.append("add_cluster")
        if zone.clusters:
            menu.append("remove_clusters")
        menu += ["delete_zone", "save"]
        return self.async_show_menu(
            step_id="zone",
            menu_options=menu,
            description_placeholders={
                "zone": self._zone_name(zone),
                "clusters": "\n".join(f"- {cluster}" for cluster in clusters) or "-",
            },
        )

    async def async_step_zone_palettes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the palettes a zone offers, at least one."""
        zone = self._current_zone
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_PALETTES]:
                zone.palettes = user_input[CONF_PALETTES]
                return await self.async_step_zone()
            errors[CONF_PALETTES] = "no_palettes"
        palettes = self._manager.palettes
        schema = probatio.Schema(
            {
                probatio.Required(CONF_PALETTES): self._palette_selector(
                    palettes.values(), multiple=True
                )
            }
        )
        chosen = [palette_id for palette_id in zone.palettes if palette_id in palettes]
        return self.async_show_form(
            step_id="zone_palettes",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or {CONF_PALETTES: chosen}
            ),
            errors=errors,
        )

    async def async_step_add_cluster(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a cluster: lamps that start with the same colour."""
        zone = self._current_zone
        clustered = {ieee for cluster in zone.clusters for ieee in cluster}
        ieee_by_entity = {
            lamp.entity_id: lamp.ieee
            for lamp in async_zone_lamps(self.hass, zone)
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
        del self._zones[self._current_zone.zone_id]
        return self._async_save()

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Save the zone."""
        return self._async_save()

    async def async_step_lamp_correction(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the light whose warmest white to correct."""
        lamps = async_color_temperature_lights(self.hass)
        if not lamps:
            return self.async_abort(reason="no_color_temperature_lights")
        if user_input is not None:
            self._lamp = lamps[user_input[CONF_LIGHT]]
            return await self.async_step_warmest_white()
        return self.async_show_form(
            step_id="lamp_correction",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_LIGHT): EntitySelector(
                        EntitySelectorConfig(include_entities=list(lamps))
                    )
                }
            ),
        )

    async def async_step_warmest_white(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set or clear the warmest white the light really shows, and save."""
        assert self._lamp is not None
        lamp = self._lamp
        assert lamp.max_mireds is not None  # only lights with colour temperature are offered
        corrections: dict[str, int] = dict(self._options.get(CONF_CORRECTIONS, {}))
        errors: dict[str, str] = {}
        if user_input is not None:
            kelvin = user_input.get(CONF_WARMEST_WHITE)
            if kelvin is None:
                corrections.pop(lamp.ieee, None)
            elif CORRECTION_PIVOT < 1_000_000 / kelvin < lamp.max_mireds:
                corrections[lamp.ieee] = round(kelvin)
            else:
                errors[CONF_WARMEST_WHITE] = "invalid_warmest_white"
            if not errors:
                self._options[CONF_CORRECTIONS] = corrections
                return self._async_save()
        schema = probatio.Schema(
            {
                probatio.Optional(CONF_WARMEST_WHITE): NumberSelector(
                    NumberSelectorConfig(
                        step=1, unit_of_measurement="K", mode=NumberSelectorMode.BOX
                    )
                )
            }
        )
        current = corrections.get(lamp.ieee)
        state = self.hass.states.get(lamp.entity_id)
        return self.async_show_form(
            step_id="warmest_white",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                user_input or ({} if current is None else {CONF_WARMEST_WHITE: current}),
            ),
            errors=errors,
            description_placeholders={
                "light": state.name if state else lamp.entity_id,
                # the valid whole Kelvin values
                "min": str(math.floor(1_000_000 / lamp.max_mireds) + 1),
                "max": str(math.ceil(1_000_000 / CORRECTION_PIVOT) - 1),
            },
        )

    @property
    def _manager(self) -> ZhaScenesManager:
        """Return the running manager."""
        manager: ZhaScenesManager = self.config_entry.runtime_data
        return manager

    @property
    def _current_zone(self) -> ZoneConfig:
        """Return the zone being edited."""
        assert self._zone is not None
        return self._zone

    @property
    def _custom_palettes(self) -> list[Palette]:
        """Return the custom palettes of the library."""
        return [
            palette
            for palette in self._manager.palettes.values()
            if palette.source is PaletteSource.CUSTOM
        ]

    @callback
    def _name_taken(self, name: str, palette: Palette | None) -> bool:
        """
        Return whether a standard scene or another palette already shows this name.

        Zones list their scenes by name.
        """
        taken = [*self._manager.standard_scene_names().values()] + [
            self._manager.palette_name(other)
            for other in self._manager.palettes.values()
            if palette is None or other.palette_id != palette.palette_id
        ]
        return name.casefold() in {other.casefold() for other in taken}

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
            if self._name_taken(user_input[CONF_NAME], palette):
                errors[CONF_NAME] = "name_taken"
            try:
                if palette is not None and user_input[CONF_COLORS] == _colors_text(palette):
                    # hex RGB is lossy: unchanged text keeps the exact colours
                    colors = [color.xy for color in palette.colors]
                else:
                    colors = _parse_colors(user_input[CONF_COLORS])
            except ValueError:
                errors[CONF_COLORS] = "invalid_colors"
            if not errors:
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
                CONF_COLORS: _colors_text(palette),
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
    def _zone_name(self, zone: ZoneConfig) -> str:
        """Return the name of a zone's ZHA group or light."""
        if zone.group_id is not None:
            return async_group_name(self.hass, zone.group_id) or f"0x{zone.group_id:04x}"
        entity_id = er.async_resolve_entity_id(er.async_get(self.hass), zone.entity_id)
        state = self.hass.states.get(entity_id) if entity_id else None
        return state.name if state else zone.zone_id

    @callback
    def _lamp_names(self, zone: ZoneConfig) -> dict[str, str]:
        """Return the names of a zone's lamps by IEEE, empty when its group or light is gone."""
        try:
            lamps = async_zone_lamps(self.hass, zone)
        except HomeAssistantError:
            return {}
        return {
            lamp.ieee: state.name
            if (state := self.hass.states.get(lamp.entity_id))
            else lamp.entity_id
            for lamp in lamps
        }

    @callback
    def _palette_selector(
        self, palettes: Iterable[Palette], *, multiple: bool = False
    ) -> SelectSelector:
        """Return a selector of palettes by name."""
        options = sorted(
            (
                SelectOptionDict(
                    value=palette.palette_id, label=self._manager.palette_name(palette)
                )
                for palette in palettes
            ),
            key=lambda option: option["label"].casefold(),
        )
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


def _colors_text(palette: Palette) -> str:
    """Return the colours of a palette as the hex RGB list shown in the form."""
    return ", ".join(_hex(color) for color in palette.colors)


def _hex(color: PaletteColor) -> str:
    """Return a palette colour as hex RGB."""
    return "#{:02x}{:02x}{:02x}".format(*color_xy_to_RGB(color.x, color.y))
