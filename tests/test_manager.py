"""Activation of palettes on zones and the default palette choice."""

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.zha_dynamic_scenes.const import DOMAIN
from custom_components.zha_dynamic_scenes.manager import DynamicScenesManager, default_palettes
from custom_components.zha_dynamic_scenes.models import LightAction, PaletteSource
from custom_components.zha_dynamic_scenes.protocol import (
    CMD_ADD_SCENE,
    SceneContent,
    add_scene_payload,
    dynamic_palette_payload,
)

from .common import BLUE, GREEN, RED, FakeTransport, lamp, palette
from .conftest import GROUP


@pytest.mark.usefixtures("zha")
async def test_activate_mixed_zone(
    hass: HomeAssistant, entry: MockConfigEntry, transport: FakeTransport
) -> None:
    manager = await DynamicScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])
    calls = async_mock_service(hass, "light", "turn_on")

    await manager.async_activate(GROUP, "custom:a")

    # start colours by entity id: lamp 1 red, 2 green, 3 blue, 4 red
    assert sorted(transport.requests) == sorted(
        [
            (
                lamp(1).ieee,
                CMD_ADD_SCENE,
                add_scene_payload(GROUP, 0x80, SceneContent.from_xy(RED.xy, 80.0), 4),
            ),
            (
                lamp(2).ieee,
                CMD_ADD_SCENE,
                add_scene_payload(GROUP, 0x80, SceneContent.from_xy(GREEN.xy, 70.0), 4),
            ),
        ]
    )
    assert transport.groupcasts == [
        (
            GROUP,
            dynamic_palette_payload(GROUP, 0x80, [RED.xy, GREEN.xy, BLUE.xy], speed=0.5),
        )
    ]
    assert sorted((call.data for call in calls), key=lambda d: d["entity_id"]) == [
        {"entity_id": "light.lamp_03", "brightness": 153, "color_temp_kelvin": 2500},
        {"entity_id": "light.lamp_04", "brightness": 204, "xy_color": RED.xy},
    ]


@pytest.mark.usefixtures("zha")
async def test_activate_overrides_and_failed_lamp_fallback(
    hass: HomeAssistant, entry: MockConfigEntry, transport: FakeTransport
) -> None:
    manager = await DynamicScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])
    calls = async_mock_service(hass, "light", "turn_on")
    transport.answers[lamp(2).ieee] = [None]

    await manager.async_activate(GROUP, "custom:a", speed=1, brightness=255, transition=1.5)

    assert transport.groupcasts == [
        (
            GROUP,
            dynamic_palette_payload(
                GROUP,
                0x80,
                [RED.xy, GREEN.xy, BLUE.xy],
                speed=1,
                brightness=254,
                transition=15,
            ),
        )
    ]
    # the dynamic lamp that could not store the scene gets its start colour statically
    assert {call.data["entity_id"]: call.data for call in calls}["light.lamp_02"] == {
        "entity_id": "light.lamp_02",
        "brightness": 255,
        "xy_color": GREEN.xy,
        "transition": 1.5,
    }
    assert len(calls) == 3


@pytest.mark.usefixtures("zha")
async def test_library_and_cache_are_stored(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict
) -> None:
    manager = await DynamicScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])
    async_mock_service(hass, "light", "turn_on")
    await manager.async_activate(GROUP, "custom:a")
    await manager.async_save()

    data = hass_storage[DOMAIN]["data"]
    assert [p["palette_id"] for p in data["palettes"]] == ["custom:a"]
    assert data["slots"][str(GROUP)]["custom:a"]["scene_id"] == 0x80
    assert set(data["scenes"]) == {lamp(1).ieee, lamp(2).ieee}

    reloaded = await DynamicScenesManager.async_load(hass, entry)
    assert reloaded.palettes == manager.palettes


def test_default_palettes_pick_best_matching_hue_group() -> None:
    ieees = [lamp(n).ieee for n in range(1, 6)]
    room = [
        palette(f"hue:room{n}", group_name="Room", actions=dict.fromkeys(ieees, LightAction()))
        for n in range(2)
    ]
    zone = palette("hue:zone", group_name="Zone", actions=dict.fromkeys(ieees[:2], LightAction()))
    other = palette("hue:other", group_name="Other", actions={"aa": LightAction()})
    custom = palette("custom:x")
    library = [*room, zone, other, custom]
    assert custom.source is PaletteSource.CUSTOM

    assert default_palettes(library, set(ieees[:2])) == [zone, custom]
    assert default_palettes(library, set(ieees)) == [*room, custom]
    assert default_palettes(library, {"unknown"}) == [custom]


@pytest.mark.usefixtures("zha")
async def test_zone_palettes(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    manager = await DynamicScenesManager.async_load(hass, entry)
    zone = manager.zones[GROUP]
    manager.async_update_palettes(
        [palette("custom:b"), palette("hue:a", actions={lamp(1).ieee: LightAction()})]
    )
    assert [p.palette_id for p in manager.zone_palettes(zone)] == ["custom:b", "hue:a"]
    zone.palettes = ["hue:a", "gone"]
    assert [p.palette_id for p in manager.zone_palettes(zone)] == ["hue:a"]
