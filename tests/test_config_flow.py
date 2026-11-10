"""Config and options flow."""

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.zha_dynamic_scenes.const import (
    CONF_HUE_APP_KEY,
    CONF_HUE_HOST,
    CONF_ZONES,
    DOMAIN,
)
from custom_components.zha_dynamic_scenes.hue_import import HueAuthError
from custom_components.zha_dynamic_scenes.models import PaletteSource

from .common import lamp, palette
from .conftest import GROUP, INTEGRATION


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
    (created,) = manager.palettes.values()
    assert created.source is PaletteSource.CUSTOM
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
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Renamed", "colors": "#ff0000,#0000ff,#00ff00", "speed": 1, "brightness": 20},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    edited = manager.palettes[created.palette_id]
    assert (edited.name, len(edited.colors), edited.speed) == ("Renamed", 3, 1)

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
    assert manager.palettes == {}


async def test_import_from_hue_bridge(hass: HomeAssistant, empty_entry: MockConfigEntry) -> None:
    result = await _menu(hass, empty_entry, "import_hue")
    user_input = {CONF_HUE_HOST: "192.0.2.10", CONF_HUE_APP_KEY: "secret"}
    with patch(f"{INTEGRATION}.config_flow.async_fetch_palettes", side_effect=HueAuthError):
        result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["errors"] == {"base": "invalid_auth"}

    imported = [palette("hue:a", actions={}), palette("hue:b", actions={})]
    with patch(f"{INTEGRATION}.config_flow.async_fetch_palettes", return_value=imported):
        result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["step_id"] == "import_done"
    assert result["description_placeholders"] == {"count": "2"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HUE_HOST] == "192.0.2.10"
    await hass.async_block_till_done()
    assert set(empty_entry.runtime_data.palettes) == {"hue:a", "hue:b"}

    result = await _menu(hass, empty_entry, "remove_palettes")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"palettes": ["hue:a"]}
    )
    assert set(empty_entry.runtime_data.palettes) == {"hue:b"}


async def test_add_zone_with_cluster(
    hass: HomeAssistant, empty_entry: MockConfigEntry, entity_registry: er.EntityRegistry
) -> None:
    group_light = entity_registry.async_get_or_create(
        "light", "zha", "light_zha_group_0xdd92", suggested_object_id="zone_group"
    )
    result = await _menu(hass, empty_entry, "add_zone")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"group": "light.zone_group"}
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
        result["flow_id"], {"next_step_id": "zone_palettes"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"automatic": False, "palettes": []}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "save"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ZONES] == [
        {
            "group_id": GROUP,
            "entity_id": group_light.id,
            "clusters": [[lamp(1).ieee, lamp(3).ieee]],
            "palettes": [],
        }
    ]
    await hass.async_block_till_done()
    assert hass.states.get("select.dynamic_scene") is not None

    # the only ZHA group is taken now
    result = await _menu(hass, empty_entry, "add_zone")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_groups"

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


async def _menu(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
