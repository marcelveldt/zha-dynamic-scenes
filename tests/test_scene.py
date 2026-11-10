"""The scene entities of the zones' palettes and the activate service."""

import logging
from unittest.mock import AsyncMock

from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import probatio
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.zha_scenes.const import CONF_ZONES, DOMAIN
from custom_components.zha_scenes.engine import TransportError
from custom_components.zha_scenes.protocol import dynamic_palette_payload

from .common import BLUE, GREEN, RED, FakeTransport, palette
from .conftest import GROUP

SCENE_A = "scene.zone_group_custom_a"
SCENE_B = "scene.zone_group_custom_b"
SCENE_BLOSSOM = "scene.zone_group_blossom"
SELECT = "select.zone_group_dynamic_scene"
COLORS = [RED.xy, GREEN.xy, BLUE.xy]


async def test_scene_per_offered_palette(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    group_device: dr.DeviceEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    await _offer(hass, loaded, ["custom:a", "custom:b", "blossom"])

    scenes = {
        entity.entity_id: (entity.unique_id, entity.device_id)
        for entity in er.async_entries_for_config_entry(entity_registry, loaded.entry_id)
        if entity.domain == "scene"
    }
    assert scenes == {
        SCENE_A: ("dd92_custom:a", group_device.id),
        SCENE_B: ("dd92_custom:b", group_device.id),
        SCENE_BLOSSOM: ("dd92_blossom", group_device.id),
    }
    assert hass.states.get(SCENE_BLOSSOM).name == "Zone group Blossom"


async def test_builtin_palette_names_are_translated(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    hass.config.language = "nl"
    await _offer(hass, loaded, ["custom:a", "blossom"])

    assert hass.states.get("scene.zone_group_bloesem").name == "Zone group Bloesem"
    assert hass.states.get(SELECT).attributes["options"] == ["Bloesem", "custom:a"]


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
    await _offer(hass, loaded, ["custom:a", "blossom"])

    await hass.services.async_call(
        "scene", "turn_on", {ATTR_ENTITY_ID: SCENE_BLOSSOM}, blocking=True
    )
    assert hass.states.get(SELECT).state == "Blossom"

    await hass.services.async_call(DOMAIN, "activate", {ATTR_ENTITY_ID: SCENE_A}, blocking=True)
    assert hass.states.get(SELECT).state == "custom:a"


async def test_scene_records_starts_from_the_select(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    async_mock_service(hass, "light", "turn_on")
    context = Context()
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: SELECT, "option": "custom:b"},
        blocking=True,
        context=context,
    )
    assert hass.states.get(SCENE_B).state != STATE_UNKNOWN
    assert hass.states.get(SCENE_B).context.id == context.id
    assert hass.states.get(SCENE_A).state == STATE_UNKNOWN


async def test_select_takes_the_context_of_a_scene_start(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    async_mock_service(hass, "light", "turn_on")
    context = Context()
    await hass.services.async_call(
        "scene", "turn_on", {ATTR_ENTITY_ID: SCENE_A}, blocking=True, context=context
    )
    assert hass.states.get(SELECT).state == "custom:a"
    assert hass.states.get(SELECT).context.id == context.id


async def test_failed_start_is_not_recorded(
    hass: HomeAssistant, loaded: MockConfigEntry, transport: FakeTransport
) -> None:
    async_mock_service(hass, "light", "turn_on")
    transport.async_start_dynamic = AsyncMock(side_effect=TransportError("down"))  # type: ignore[method-assign]
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("scene", "turn_on", {ATTR_ENTITY_ID: SCENE_A}, blocking=True)
    assert hass.states.get(SCENE_A).state == STATE_UNKNOWN


async def test_scenes_follow_the_library(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = loaded.runtime_data
    manager.async_update_palettes([palette("custom:b", name="Renamed")])
    manager.async_remove_palettes(["custom:a"])
    await hass.async_block_till_done()

    assert hass.states.get(SCENE_A) is None
    assert entity_registry.async_get(SCENE_A) is None
    assert hass.states.get(SCENE_B).name == "Zone group Renamed"
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]

    manager.async_update_palettes([palette("custom:a")])
    await hass.async_block_till_done()
    assert hass.states.get(SCENE_A) is not None


async def test_setup_removes_scenes_no_longer_offered(
    hass: HomeAssistant, loaded: MockConfigEntry, entity_registry: er.EntityRegistry
) -> None:
    kept = {entity_id: entity_registry.async_get(entity_id).id for entity_id in (SCENE_A, SELECT)}
    await _offer(hass, loaded, ["custom:a"])

    assert entity_registry.async_get(SCENE_B) is None
    # neither platform removed the other's entities
    assert {
        entity_id: entity_registry.async_get(entity_id).id for entity_id in (SCENE_A, SELECT)
    } == kept


async def _offer(hass: HomeAssistant, entry: MockConfigEntry, palettes: list[str]) -> None:
    """Let the zone offer these palettes, as the options flow does."""
    zone = {**entry.options[CONF_ZONES][0], "palettes": palettes}
    hass.config_entries.async_update_entry(entry, options={CONF_ZONES: [zone]})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
