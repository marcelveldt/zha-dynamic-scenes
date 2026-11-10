"""Config and options flow."""

from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant.common import MockConfigEntry, get_schema_suggested_value

from custom_components.zha_scenes.const import CONF_CORRECTIONS, CONF_ZONES, DOMAIN
from custom_components.zha_scenes.models import LampKind, PaletteSource

from .common import lamp, palette
from .conftest import GROUP, INTEGRATION, ZONE_ID


@pytest.mark.usefixtures("zha")
async def test_user_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_ZONES: []}


async def test_user_flow_without_zha(hass: HomeAssistant) -> None:
    hass.config.components.add("zha")
    with patch(f"{INTEGRATION}.config_flow.async_zha_ready", return_value=False):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "zha_not_ready"


@pytest.fixture
async def empty_entry(hass: HomeAssistant, zha: None) -> MockConfigEntry:
    """Set up an entry without zones."""
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_ZONES: []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def test_custom_palette_add_edit_delete(
    hass: HomeAssistant, empty_entry: MockConfigEntry
) -> None:
    manager = empty_entry.runtime_data
    result = await _menu(hass, empty_entry, "add_palette")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Mine", "colors": "#ff0000 #00ff00", "speed": 0.4, "brightness": 50},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (created,) = (p for p in manager.palettes.values() if p.source is PaletteSource.CUSTOM)
    assert created.name == "Mine"
    assert [round(c.x, 3) for c in created.colors] == [0.701, 0.172]
    assert {c.brightness for c in created.colors} == {50}

    result = await _menu(hass, empty_entry, "edit_palette")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"palette": created.palette_id}
    )
    assert result["step_id"] == "palette"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Mine", "colors": "bad", "speed": 0.4, "brightness": 50},
    )
    assert result["errors"] == {"colors": "invalid_colors"}
    # zones list palettes by name, so the name of another palette is refused
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "blossom", "colors": "#ff0000,#0000ff", "speed": 0.4, "brightness": 50},
    )
    assert result["errors"] == {"name": "name_taken"}
    # nor the name of a standard scene
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "relax", "colors": "#ff0000,#0000ff", "speed": 0.4, "brightness": 50},
    )
    assert result["errors"] == {"name": "name_taken"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Renamed", "colors": "#ff0000,#0000ff,#00ff00", "speed": 1, "brightness": 20},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    edited = manager.palettes[created.palette_id]
    assert (edited.name, len(edited.colors), edited.speed) == ("Renamed", 3, 1)

    # saving the colours as shown keeps them exactly; hex RGB would shift them
    result = await _menu(hass, empty_entry, "edit_palette")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"palette": created.palette_id}
    )
    shown = get_schema_suggested_value(result["data_schema"].schema, "colors")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Renamed", "colors": shown, "speed": 0.5, "brightness": 20}
    )
    assert [c.xy for c in manager.palettes[created.palette_id].colors] == [
        c.xy for c in edited.colors
    ]

    result = await _menu(hass, empty_entry, "edit_palette")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"palette": created.palette_id}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "Renamed",
            "colors": "#ff0000,#0000ff",
            "speed": 1,
            "brightness": 20,
            "delete": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert created.palette_id not in manager.palettes


async def test_remove_custom_palettes(hass: HomeAssistant, empty_entry: MockConfigEntry) -> None:
    manager = empty_entry.runtime_data
    result = await hass.config_entries.options.async_init(empty_entry.entry_id)
    # built-in palettes can be neither edited nor removed
    assert result["menu_options"] == ["add_palette", "add_zone", "lamp_correction"]

    manager.async_update_palettes([palette("custom:a", name="Mine"), palette("custom:b")])
    result = await _menu(hass, empty_entry, "remove_palettes")
    assert _field(result, "palettes").config["options"] == [
        {"value": "custom:b", "label": "custom:b"},
        {"value": "custom:a", "label": "Mine"},
    ]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"palettes": ["custom:a"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "custom:a" not in manager.palettes
    assert "custom:b" in manager.palettes


async def test_add_zone_with_cluster(
    hass: HomeAssistant, empty_entry: MockConfigEntry, entity_registry: er.EntityRegistry
) -> None:
    group_light = entity_registry.async_get_or_create(
        "light", "zha", "light_zha_group_0xdd92", suggested_object_id="zone_group"
    )
    empty_entry.runtime_data.async_update_palettes([palette(name="Mine")])
    result = await _menu(hass, empty_entry, "add_zone")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"light": "light.zone_group"}
    )
    # a new zone first gets its palettes: all of them on offer by name, none chosen
    assert result["step_id"] == "zone_palettes"
    labels = [option["label"] for option in _field(result, "palettes").config["options"]]
    assert len(labels) == 45
    assert labels == sorted(labels, key=str.casefold)
    assert labels[:2] == ["Amber bloom", "Arise"]
    assert "Mine" in labels
    assert not get_schema_suggested_value(result["data_schema"].schema, "palettes")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"palettes": []})
    assert result["errors"] == {"palettes": "no_palettes"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"palettes": ["rolling_hills"]}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "zone"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_cluster"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"lights": ["light.lamp_01"]}
    )
    assert result["errors"] == {"lights": "cluster_too_small"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"lights": ["light.lamp_01", "light.lamp_03"]}
    )
    assert result["step_id"] == "zone"
    assert result["description_placeholders"]["clusters"] == "- light.lamp_01, light.lamp_03"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "save"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ZONES] == [
        {
            "group_id": GROUP,
            "entity_id": group_light.id,
            "clusters": [[lamp(1).ieee, lamp(3).ieee]],
            "palettes": ["rolling_hills"],
        }
    ]
    await hass.async_block_till_done()
    assert hass.states.get("select.scene") is not None
    assert hass.states.get("scene.rolling_hills") is not None
    # storage keys are built from the group id, so it must not stay zigpy's hex printing type
    assert type(empty_entry.runtime_data.zones[ZONE_ID].group_id) is int

    # the only ZHA group is taken now
    result = await _menu(hass, empty_entry, "add_zone")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_lights"

    result = await _menu(hass, empty_entry, "edit_zone")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"zone": str(GROUP)}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_clusters"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"clusters": ["0"]}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_zone"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ZONES] == []


async def test_add_single_light_zone(
    hass: HomeAssistant, empty_entry: MockConfigEntry, entity_registry: er.EntityRegistry
) -> None:
    light = entity_registry.async_get_or_create(
        "light", "zha", f"{lamp(1).ieee}-11", suggested_object_id="lamp_01"
    )
    hass.states.async_set("light.lamp_01", "on", {"friendly_name": "Lamp 01"})
    device_lights = {"light.lamp_01": lamp(1).ieee}
    with patch(f"{INTEGRATION}.config_flow.async_device_lights", return_value=device_lights):
        result = await _menu(hass, empty_entry, "add_zone")
        assert _field(result, "light").config["include_entities"] == [
            "light.zone_group",
            "light.lamp_01",
        ]
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"light": "light.lamp_01"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"palettes": ["ibiza"]}
        )
        assert result["step_id"] == "zone"
        assert result["description_placeholders"]["zone"] == "Lamp 01"
        # a single light has nothing to cluster
        assert result["menu_options"] == ["zone_palettes", "delete_zone", "save"]
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "save"}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ZONES] == [
            {
                "group_id": None,
                "entity_id": light.id,
                "clusters": [],
                "palettes": ["ibiza"],
                "ieee": lamp(1).ieee,
            }
        ]
        await hass.async_block_till_done()
        assert hass.states.get("select.scene") is not None

        # the light is a zone now, the group is still free
        result = await _menu(hass, empty_entry, "add_zone")
        assert _field(result, "light").config["include_entities"] == ["light.zone_group"]

    result = await _menu(hass, empty_entry, "edit_zone")
    assert _field(result, "zone").config["options"] == [{"value": lamp(1).ieee, "label": "Lamp 01"}]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"zone": lamp(1).ieee}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_zone"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ZONES] == []


async def test_edit_zone_palettes(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    loaded.runtime_data.async_remove_palettes(["custom:a"])
    result = await _menu(hass, loaded, "edit_zone")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"zone": ZONE_ID})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "zone_palettes"}
    )
    # the zone's palettes are chosen, except one removed from the library
    assert get_schema_suggested_value(result["data_schema"].schema, "palettes") == ["custom:b"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"palettes": []})
    assert result["errors"] == {"palettes": "no_palettes"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"palettes": ["custom:b", "ibiza"]}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "save"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ZONES][0]["palettes"] == ["custom:b", "ibiza"]


async def test_delete_zone_whose_light_is_gone(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    removed = HomeAssistantError("the zone's light was removed from ZHA")
    with patch(f"{INTEGRATION}.config_flow.async_zone_lamps", side_effect=removed):
        result = await _menu(hass, loaded, "edit_zone")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"zone": ZONE_ID}
        )
        assert result["step_id"] == "zone"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "delete_zone"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ZONES] == []


async def test_lamp_correction(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    controller = lamp(3, LampKind.COLOR_TEMP, signify=False, max_mireds=495)
    hass.states.async_set("light.lamp_03", "on", {"friendly_name": "Strip"})
    zones = loaded.options[CONF_ZONES]
    with patch(
        f"{INTEGRATION}.config_flow.async_color_temperature_lights",
        return_value={"light.lamp_03": controller},
    ):
        result = await _menu(hass, loaded, "lamp_correction")
        assert _field(result, "light").config["include_entities"] == ["light.lamp_03"]
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"light": "light.lamp_03"}
        )
        assert result["step_id"] == "warmest_white"
        # whole Kelvin values warmer than 300 mired and colder than the warmest setting
        assert result["description_placeholders"] == {
            "light": "Strip",
            "min": "2021",
            "max": "3333",
        }
        for kelvin in (0, 2020, 3334):
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"warmest_white": kelvin}
            )
            assert result["errors"] == {"warmest_white": "invalid_warmest_white"}
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"warmest_white": 2237.0}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"] == {CONF_ZONES: zones, CONF_CORRECTIONS: {controller.ieee: 2237}}
        await hass.async_block_till_done()

        # the current correction is shown, and an empty value clears it
        result = await _menu(hass, loaded, "lamp_correction")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"light": "light.lamp_03"}
        )
        assert get_schema_suggested_value(result["data_schema"].schema, "warmest_white") == 2237
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"] == {CONF_ZONES: zones, CONF_CORRECTIONS: {}}


async def test_lamp_correction_without_colour_temperature_lights(
    hass: HomeAssistant, empty_entry: MockConfigEntry
) -> None:
    with patch(f"{INTEGRATION}.config_flow.async_color_temperature_lights", return_value={}):
        result = await _menu(hass, empty_entry, "lamp_correction")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_color_temperature_lights"


def _field(result: dict, key: str) -> Any:
    return next(value for marker, value in result["data_schema"].schema.items() if marker == key)


async def _menu(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
