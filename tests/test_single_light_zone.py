"""A zone of one ZHA light next to a group zone: stored form, entities and the unicast start."""

from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.zha_scenes.const import CONF_ZONES, DOMAIN
from custom_components.zha_scenes.models import ZoneConfig
from custom_components.zha_scenes.protocol import (
    CMD_ENHANCED_ADD_SCENE,
    SceneContent,
    add_scene_payload,
    dynamic_palette_payload,
)

from .common import BLUE, GREEN, RED, FakeTransport, lamp
from .conftest import GROUP

IEEE = lamp(1).ieee
COLORS = [RED.xy, GREEN.xy, BLUE.xy]
GROUP_ZONE = {
    "group_id": GROUP,
    "entity_id": "light.zone_group",
    "clusters": [],
    "palettes": ["custom:a", "custom:b"],
}


@pytest.fixture
def lamp_light(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry, entity_registry: er.EntityRegistry
) -> er.RegistryEntry:
    """Register the device and the light of lamp 1, as ZHA does."""
    zha_entry = MockConfigEntry(domain="zha")
    zha_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=zha_entry.entry_id, identifiers={("zha", IEEE)}, name="Lamp 01"
    )
    return entity_registry.async_get_or_create(
        "light",
        "zha",
        f"{IEEE}-11",
        suggested_object_id="lamp_01",
        config_entry=zha_entry,
        device_id=device.id,
    )


@pytest.fixture
def entry(hass: HomeAssistant, lamp_light: er.RegistryEntry) -> MockConfigEntry:
    """Return the config entry with the group zone and a zone of lamp 1 alone."""
    light_zone = {**GROUP_ZONE, "group_id": None, "entity_id": lamp_light.id, "ieee": IEEE}
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_ZONES: [GROUP_ZONE, light_zone]})
    entry.add_to_hass(hass)
    return entry


def test_zone_ids_and_stored_form() -> None:
    group = ZoneConfig.from_dict(GROUP_ZONE)
    assert (group.zone_id, group.unique_id) == (str(GROUP), "dd92")
    assert group.to_dict() == GROUP_ZONE

    stored = {**GROUP_ZONE, "group_id": None, "palettes": ["custom:a"], "ieee": IEEE}
    single = ZoneConfig.from_dict(stored)
    assert (single.zone_id, single.unique_id) == (IEEE, IEEE)
    assert single.to_dict() == stored


async def test_entities_on_the_lamp_device(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    group_device: dr.DeviceEntry,
    lamp_light: er.RegistryEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    entities = {
        entity.unique_id: (entity.entity_id, entity.device_id)
        for entity in er.async_entries_for_config_entry(entity_registry, loaded.entry_id)
    }
    assert entities == {
        "dd92": ("select.zone_group_dynamic_scene", group_device.id),
        "dd92_custom:a": ("scene.zone_group_custom_a", group_device.id),
        "dd92_custom:b": ("scene.zone_group_custom_b", group_device.id),
        IEEE: ("select.lamp_01_dynamic_scene", lamp_light.device_id),
        f"{IEEE}_custom:a": ("scene.lamp_01_custom_a", lamp_light.device_id),
        f"{IEEE}_custom:b": ("scene.lamp_01_custom_b", lamp_light.device_id),
    }


async def test_scene_starts_the_palette_with_a_unicast(
    hass: HomeAssistant, loaded: MockConfigEntry, transport: FakeTransport, hass_storage: dict
) -> None:
    calls = async_mock_service(hass, "light", "turn_on")
    scene = {ATTR_ENTITY_ID: "scene.lamp_01_custom_a"}
    for _ in range(2):
        await hass.services.async_call("scene", "turn_on", scene, blocking=True)

    payload = dynamic_palette_payload(0, 0x80, COLORS, speed=0.5)
    assert transport.unicasts == [(IEEE, payload), (IEEE, payload)]
    assert transport.groupcasts == []
    write = (
        IEEE,
        CMD_ENHANCED_ADD_SCENE,
        add_scene_payload(0, 0x80, SceneContent.from_xy(RED.xy, 80.0), 4),
    )
    assert transport.requests == [write]
    assert calls == []
    assert hass.states.get("select.lamp_01_dynamic_scene").state == "custom:a"
    assert hass.states.get("select.zone_group_dynamic_scene").state == STATE_UNKNOWN

    await hass.services.async_call(
        DOMAIN, "rewrite_scenes", {ATTR_ENTITY_ID: "select.lamp_01_dynamic_scene"}, blocking=True
    )
    await hass.services.async_call("scene", "turn_on", scene, blocking=True)
    assert transport.requests == [write, write]

    # the group zone holding the same lamp gets a scene id of its own
    await hass.services.async_call(
        "scene", "turn_on", {ATTR_ENTITY_ID: "scene.zone_group_custom_a"}, blocking=True
    )
    assert transport.groupcasts == [(GROUP, dynamic_palette_payload(0, 0x81, COLORS, speed=0.5))]
    await loaded.runtime_data.async_save()
    slots = hass_storage[DOMAIN]["data"]["slots"]
    assert {zone: palettes["custom:a"]["scene_id"] for zone, palettes in slots.items()} == {
        IEEE: 0x80,
        str(GROUP): 0x81,
    }
