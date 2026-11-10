"""Activation of palettes on zones and the default palette choice."""

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.zha_dynamic_scenes.const import DOMAIN
from custom_components.zha_dynamic_scenes.manager import DynamicScenesManager, default_palettes
from custom_components.zha_dynamic_scenes.models import (
    Gradient,
    GradientMode,
    LightAction,
    PaletteSource,
)
from custom_components.zha_dynamic_scenes.protocol import (
    CMD_ADD_HUE_SCENE,
    CMD_ENHANCED_ADD_SCENE,
    SceneContent,
    add_scene_payload,
    dynamic_palette_payload,
)

from .common import BLUE, FEATURES_GRADIENT, GREEN, RED, FakeTransport, lamp, palette
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
                CMD_ENHANCED_ADD_SCENE,
                add_scene_payload(0, 0x80, SceneContent.from_xy(RED.xy, 80.0), 4),
            ),
            (
                lamp(2).ieee,
                CMD_ENHANCED_ADD_SCENE,
                add_scene_payload(0, 0x80, SceneContent.from_xy(GREEN.xy, 70.0), 4),
            ),
        ]
    )
    assert transport.groupcasts == [
        (
            GROUP,
            dynamic_palette_payload(0, 0x80, [RED.xy, GREEN.xy, BLUE.xy], speed=0.5),
        )
    ]
    assert sorted((call.data for call in calls), key=lambda d: d["entity_id"]) == [
        {"entity_id": "light.lamp_03", "brightness": 153, "color_temp_kelvin": 2500},
        {"entity_id": "light.lamp_04", "brightness": 203, "xy_color": RED.xy},
    ]
    assert transport.signify_requests == []


@pytest.mark.usefixtures("zha")
async def test_activate_gradient_lamp(
    hass: HomeAssistant, entry: MockConfigEntry, transport: FakeTransport, hass_storage: dict
) -> None:
    manager = await DynamicScenesManager.async_load(hass, entry)
    gradient = Gradient(
        points=((0.50, 0.40), (0.25, 0.25), (0.40, 0.20)), mode=GradientMode.SEGMENTED_PALETTE
    )
    actions = {lamp(1).ieee: LightAction(xy=(0.50, 0.40), brightness=40.0, gradient=gradient)}
    manager.async_update_palettes([palette("hue:a", actions=actions)])
    calls = async_mock_service(hass, "light", "turn_on")
    transport.features[lamp(1).ieee] = FEATURES_GRADIENT

    await manager.async_activate(GROUP, "hue:a")

    # the gradient lamp stores its gradient like the Hue bridge does
    assert transport.signify_requests == [
        (
            lamp(1).ieee,
            CMD_ADD_HUE_SCENE,
            # group 0x0000, scene 0x80, then what the bridge sent for 40 % segmented
            bytes.fromhex("000080" + "5301016604000d30060000e2ba7a71b54cb5583d1800"),
        )
    ]
    assert transport.requests == [
        (
            lamp(2).ieee,
            CMD_ENHANCED_ADD_SCENE,
            add_scene_payload(0, 0x80, SceneContent.from_xy(RED.xy, 80.0), 4),
        )
    ]
    assert transport.groupcasts == [
        (GROUP, dynamic_palette_payload(0, 0x80, [RED.xy, GREEN.xy, BLUE.xy], speed=0.5))
    ]
    assert sorted(call.data["entity_id"] for call in calls) == ["light.lamp_03", "light.lamp_04"]

    await manager.async_save()
    assert hass_storage[DOMAIN]["data"]["gradient"] == {lamp(1).ieee: True, lamp(2).ieee: False}


@pytest.mark.usefixtures("zha")
async def test_failed_gradient_lamp_gets_its_first_gradient_point(
    hass: HomeAssistant, entry: MockConfigEntry, transport: FakeTransport
) -> None:
    manager = await DynamicScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])
    calls = async_mock_service(hass, "light", "turn_on")
    transport.features[lamp(2).ieee] = FEATURES_GRADIENT
    transport.answers[lamp(2).ieee] = [None]

    await manager.async_activate(GROUP, "custom:a")

    # its start colour is green; its gradient is the palette, which starts red
    assert {call.data["entity_id"]: call.data for call in calls}["light.lamp_02"] == {
        "entity_id": "light.lamp_02",
        "brightness": 178,
        "xy_color": RED.xy,
    }


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
                0,
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
async def test_failing_light_does_not_stop_the_activation(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    transport: FakeTransport,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = await DynamicScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])

    async def turn_on(call: ServiceCall) -> None:
        if call.data["entity_id"] == "light.lamp_03":
            raise HomeAssistantError("unreachable")

    hass.services.async_register("light", "turn_on", turn_on)
    await manager.async_activate(GROUP, "custom:a")
    assert len(transport.groupcasts) == 1
    assert "Could not set light.lamp_03 to its start state: unreachable" in caplog.text


@pytest.mark.usefixtures("zha")
async def test_reimport_keeps_actions_of_lamps_the_bridge_lost(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await DynamicScenesManager.async_load(hass, entry)
    moved, kept = LightAction(xy=(0.1, 0.2)), LightAction(xy=(0.5, 0.4))
    manager.async_update_palettes(
        [
            palette(
                "hue:a",
                group_name="Room",
                actions={lamp(1).ieee: moved, lamp(2).ieee: LightAction(xy=(0.3, 0.4))},
            )
        ]
    )
    manager.async_update_palettes([palette("hue:a", actions={lamp(2).ieee: kept})])
    merged = manager.palettes["hue:a"]
    assert merged.actions == {lamp(1).ieee: moved, lamp(2).ieee: kept}
    assert merged.group_name == "Room"


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
