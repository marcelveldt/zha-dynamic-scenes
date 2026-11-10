"""Fixtures for ZHA Scenes."""

from collections.abc import Generator
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant.common import MockConfigEntry
from zigpy.types import Group

from custom_components.zha_scenes.const import CONF_ZONES, DOMAIN
from custom_components.zha_scenes.models import Lamp, LampKind, ZoneConfig

from .common import FakeTransport, lamp, palette

INTEGRATION = "custom_components.zha_scenes"
GROUP = 0xDD92
ZONE_ID = str(GROUP)


@pytest.fixture
def lamps() -> list[Lamp]:
    """Lamps of the zone: two dynamic, one colour temperature, one other colour lamp."""
    return [lamp(1), lamp(2), lamp(3, LampKind.COLOR_TEMP), lamp(4, LampKind.COLOR)]


@pytest.fixture
def transport() -> FakeTransport:
    """Return the fake Zigbee transport."""
    return FakeTransport()


@pytest.fixture
def zha(hass: HomeAssistant, lamps: list[Lamp], transport: FakeTransport) -> Generator[None]:
    """Pretend ZHA runs with one group holding the lamps; a single light zone is one lamp."""
    hass.config.components.add("zha")

    def zone_lamps(hass: HomeAssistant, zone: ZoneConfig) -> list[Lamp]:
        return lamps if zone.ieee is None else [lamp for lamp in lamps if lamp.ieee == zone.ieee]

    with (
        patch(f"{INTEGRATION}.async_zha_ready", return_value=True),
        patch(f"{INTEGRATION}.config_flow.async_zha_ready", return_value=True),
        patch(f"{INTEGRATION}.manager.ZhaSceneTransport", return_value=transport),
        patch(f"{INTEGRATION}.manager.async_zone_lamps", side_effect=zone_lamps),
        patch(f"{INTEGRATION}.config_flow.async_zone_lamps", side_effect=zone_lamps),
        patch(
            f"{INTEGRATION}.config_flow.async_group_lights",
            # ZHA hands out zigpy's group id type, an int that prints as hex
            return_value={"light.zone_group": Group(GROUP)},
        ),
        patch(f"{INTEGRATION}.config_flow.async_device_lights", return_value={}),
        patch(f"{INTEGRATION}.config_flow.async_group_name", return_value="Zone group"),
        patch(f"{INTEGRATION}.async_listen_scene_losses", return_value=lambda: None),
    ):
        yield


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return the config entry with one zone that offers palettes `custom:a` and `custom:b`."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_ZONES: [
                {
                    "group_id": GROUP,
                    "entity_id": "light.zone_group",
                    "clusters": [],
                    "palettes": ["custom:a", "custom:b"],
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def group_device(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry, entity_registry: er.EntityRegistry
) -> dr.DeviceEntry:
    """Register the device and the light of the zone's ZHA group, as ZHA does."""
    zha_entry = MockConfigEntry(domain="zha")
    zha_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=zha_entry.entry_id,
        identifiers={("zha", f"{GROUP:04x}")},
        name="Zone group",
    )
    entity_registry.async_get_or_create(
        "light",
        "zha",
        f"light_zha_group_0x{GROUP:04x}",
        suggested_object_id="zone_group",
        config_entry=zha_entry,
        device_id=device.id,
    )
    return device


@pytest.fixture
async def loaded(
    hass: HomeAssistant, entry: MockConfigEntry, zha: None, group_device: dr.DeviceEntry
) -> MockConfigEntry:
    """Set up the entry with two palettes in the library."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    entry.runtime_data.async_update_palettes([palette("custom:a"), palette("custom:b")])
    await hass.async_block_till_done()
    return entry
