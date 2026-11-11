"""Activation of palettes on zones and the palette library."""

import asyncio

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.zha_scenes.const import DOMAIN, SIGNAL_ZONE_ACTIVATED
from custom_components.zha_scenes.manager import ZhaScenesManager
from custom_components.zha_scenes.models import GradientMode, PaletteSource
from custom_components.zha_scenes.protocol import (
    CMD_ADD_HUE_SCENE,
    CMD_ENHANCED_ADD_SCENE,
    GradientSceneContent,
    SceneContent,
    add_scene_payload,
    dynamic_palette_payload,
    gradient_scene_payload,
)

from .common import BLUE, FEATURES_GRADIENT, GREEN, RED, FakeTransport, lamp, palette
from .conftest import GROUP, ZONE_ID


@pytest.mark.usefixtures("zha")
async def test_activate_mixed_zone(
    hass: HomeAssistant, entry: MockConfigEntry, transport: FakeTransport
) -> None:
    manager = await ZhaScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])
    calls = async_mock_service(hass, "light", "turn_on")

    await manager.async_activate(ZONE_ID, "custom:a")

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
    manager = await ZhaScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])
    calls = async_mock_service(hass, "light", "turn_on")
    transport.features[lamp(1).ieee] = FEATURES_GRADIENT

    await manager.async_activate(ZONE_ID, "custom:a")

    # the gradient lamp stores the palette colours as its gradient, along the shortest path
    gradient = GradientSceneContent.from_gradient(
        [RED.xy, BLUE.xy, GREEN.xy], GradientMode.INTERPOLATED_PALETTE, 80.0
    )
    assert transport.signify_requests == [
        (lamp(1).ieee, CMD_ADD_HUE_SCENE, gradient_scene_payload(0, 0x80, gradient, 4))
    ]
    assert transport.requests == [
        (
            lamp(2).ieee,
            CMD_ENHANCED_ADD_SCENE,
            add_scene_payload(0, 0x80, SceneContent.from_xy(GREEN.xy, 70.0), 4),
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
    manager = await ZhaScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])
    calls = async_mock_service(hass, "light", "turn_on")
    transport.features[lamp(2).ieee] = FEATURES_GRADIENT
    transport.answers[lamp(2).ieee] = [None]

    await manager.async_activate(ZONE_ID, "custom:a")

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
    manager = await ZhaScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])
    calls = async_mock_service(hass, "light", "turn_on")
    transport.answers[lamp(2).ieee] = [None]

    await manager.async_activate(ZONE_ID, "custom:a", speed=1, brightness=255, transition=1.5)

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
    manager = await ZhaScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])

    async def turn_on(call: ServiceCall) -> None:
        if call.data["entity_id"] == "light.lamp_03":
            raise HomeAssistantError("unreachable")

    hass.services.async_register("light", "turn_on", turn_on)
    await manager.async_activate(ZONE_ID, "custom:a")
    assert len(transport.groupcasts) == 1
    assert "Could not set light.lamp_03 to its start state: unreachable" in caplog.text


@pytest.mark.usefixtures("zha")
async def test_activations_of_a_zone_run_one_after_another(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await ZhaScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette("custom:a"), palette("custom:b")])
    started: list[str] = []
    async_dispatcher_connect(
        hass, SIGNAL_ZONE_ACTIVATED.format(GROUP), lambda palette_id, _: started.append(palette_id)
    )
    release = asyncio.Event()
    first_waits = asyncio.Event()
    blocked: list[str] = []
    hold = True

    async def turn_on(call: ServiceCall) -> None:
        if hold:
            blocked.append(call.data["entity_id"])
            if len(blocked) == 2:  # the first start waits on its two static lights
                first_waits.set()
            await release.wait()

    hass.services.async_register("light", "turn_on", turn_on)
    first = hass.async_create_task(manager.async_activate(ZONE_ID, "custom:a"))
    await first_waits.wait()
    hold = False
    second = hass.async_create_task(manager.async_activate(ZONE_ID, "custom:b"))
    for _ in range(20):
        await asyncio.sleep(0)
    release.set()
    await first
    await second
    # the later start finishes last, so the zone shows it
    assert started == ["custom:a", "custom:b"]


@pytest.mark.usefixtures("zha")
async def test_library_and_cache_are_stored(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict
) -> None:
    manager = await ZhaScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette()])
    async_mock_service(hass, "light", "turn_on")
    await manager.async_activate(ZONE_ID, "custom:a")
    await manager.async_save()

    data = hass_storage[DOMAIN]["data"]
    # the built-in palettes are not stored
    assert manager.palettes["rolling_hills"].source is PaletteSource.BUILTIN
    assert [p["palette_id"] for p in data["palettes"]] == ["custom:a"]
    assert data["slots"][str(GROUP)]["custom:a"]["scene_id"] == 0x80
    assert set(data["scenes"]) == {lamp(1).ieee, lamp(2).ieee}

    reloaded = await ZhaScenesManager.async_load(hass, entry)
    assert reloaded.palettes == manager.palettes


@pytest.mark.usefixtures("zha")
async def test_zone_palettes(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    manager = await ZhaScenesManager.async_load(hass, entry)
    zone = manager.zones[ZONE_ID]
    manager.async_update_palettes([palette("custom:a", name="apple"), palette("custom:b")])
    zone.palettes = ["custom:b", "rolling_hills", "gone", "custom:a"]
    # sorted by name; palettes no longer in the library are left out
    assert [p.palette_id for p in manager.zone_palettes(zone)] == [
        "custom:a",
        "custom:b",
        "rolling_hills",
    ]


@pytest.mark.usefixtures("zha")
async def test_palette_names_follow_the_language(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    hass.config.language = "nl"
    manager = await ZhaScenesManager.async_load(hass, entry)
    manager.async_update_palettes([palette(name="Mine")])
    assert manager.palette_name(manager.palettes["rolling_hills"]) == "Glooiende heuvels"
    assert manager.palette_name(manager.palettes["custom:a"]) == "Mine"
