"""The scene entities of the zones' palettes and the activate service."""

import logging

from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import probatio
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.zha_dynamic_scenes.const import CONF_ZONES, DOMAIN
from custom_components.zha_dynamic_scenes.models import Palette
from custom_components.zha_dynamic_scenes.protocol import dynamic_palette_payload

from .common import BLUE, GREEN, RED, FakeTransport, palette
from .conftest import GROUP

SCENE_A = "scene.zone_group_custom_a"
SCENE_B = "scene.zone_group_custom_b"
SELECT = "select.zone_group_dynamic_scene"
COLORS = [RED.xy, GREEN.xy, BLUE.xy]


async def test_scene_per_offered_palette(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    group_device: dr.DeviceEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    loaded.runtime_data.async_update_palettes([_blossom()])
    await hass.async_block_till_done()

    scenes = {
        entity.entity_id: (entity.unique_id, entity.device_id)
        for entity in er.async_entries_for_config_entry(entity_registry, loaded.entry_id)
        if entity.domain == "scene"
    }
    assert scenes == {
        SCENE_A: ("dd92_custom:a", group_device.id),
        SCENE_B: ("dd92_custom:b", group_device.id),
        "scene.zone_group_blossom": ("dd92_custom:c", group_device.id),
    }
    assert hass.states.get("scene.zone_group_blossom").name == "Zone group Blossom"


async def test_turn_on_starts_the_palette(
    hass: HomeAssistant, loaded: MockConfigEntry, transport: FakeTransport
) -> None:
    calls = async_mock_service(hass, "light", "turn_on")
    await hass.services.async_call(
        "scene", "turn_on", {ATTR_ENTITY_ID: SCENE_B, "transition": 2.5}, blocking=True
    )

    assert transport.groupcasts == [
        (GROUP, dynamic_palette_payload(0, 0x80, COLORS, speed=0.5, transition=25))
    ]
    assert [call.data["transition"] for call in calls] == [2.5, 2.5]
    assert hass.states.get(SCENE_B).state != STATE_UNKNOWN


async def test_activate_service(
    hass: HomeAssistant, loaded: MockConfigEntry, transport: FakeTransport
) -> None:
    calls = async_mock_service(hass, "light", "turn_on")
    await hass.services.async_call(
        DOMAIN,
        "activate",
        {ATTR_ENTITY_ID: SCENE_A, "speed": 0.25, "brightness": 50, "transition": 1.5},
        blocking=True,
    )

    assert transport.groupcasts == [
        (
            GROUP,
            dynamic_palette_payload(0, 0x80, COLORS, speed=0.25, brightness=50, transition=15),
        )
    ]
    assert [(call.data["brightness"], call.data["transition"]) for call in calls] == [
        (50, 1.5),
        (50, 1.5),
    ]
    assert hass.states.get(SCENE_A).state != STATE_UNKNOWN

    with pytest.raises(probatio.Invalid):
        await hass.services.async_call(
            DOMAIN, "activate", {ATTR_ENTITY_ID: SCENE_A, "speed": 1.5}, blocking=True
        )


async def test_select_shows_the_palette_started_last(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    async_mock_service(hass, "light", "turn_on")
    loaded.runtime_data.async_update_palettes([_blossom()])
    await hass.async_block_till_done()

    await hass.services.async_call(
        "scene", "turn_on", {ATTR_ENTITY_ID: "scene.zone_group_blossom"}, blocking=True
    )
    assert hass.states.get(SELECT).state == "Blossom (Living room)"

    await hass.services.async_call(DOMAIN, "activate", {ATTR_ENTITY_ID: SCENE_A}, blocking=True)
    assert hass.states.get(SELECT).state == "custom:a"


async def test_scenes_follow_the_library(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = loaded.runtime_data
    manager.async_update_palettes([palette("custom:b", name="Renamed"), _blossom()])
    manager.async_remove_palettes(["custom:a"])
    await hass.async_block_till_done()

    assert hass.states.get(SCENE_A) is None
    assert entity_registry.async_get(SCENE_A) is None
    assert hass.states.get(SCENE_B).name == "Zone group Renamed"
    assert hass.states.get("scene.zone_group_blossom") is not None
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]

    manager.async_update_palettes([palette("custom:a")])
    await hass.async_block_till_done()
    assert hass.states.get(SCENE_A) is not None


async def test_setup_removes_scenes_no_longer_offered(
    hass: HomeAssistant, loaded: MockConfigEntry, entity_registry: er.EntityRegistry
) -> None:
    kept = {entity_id: entity_registry.async_get(entity_id).id for entity_id in (SCENE_A, SELECT)}
    zone = {**loaded.options[CONF_ZONES][0], "palettes": ["custom:a"]}
    hass.config_entries.async_update_entry(loaded, options={CONF_ZONES: [zone]})
    assert await hass.config_entries.async_reload(loaded.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get(SCENE_B) is None
    # neither platform removed the other's entities
    assert {
        entity_id: entity_registry.async_get(entity_id).id for entity_id in (SCENE_A, SELECT)
    } == kept


def _blossom() -> Palette:
    """Return a palette with the Hue room in its label."""
    return palette("custom:c", name="Blossom", group_name="Living room")
