"""Fixtures for ZHA dynamic scenes."""

from collections.abc import Generator
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant.common import MockConfigEntry
from zigpy.types import Group

from custom_components.zha_dynamic_scenes.const import CONF_ZONES, DOMAIN
from custom_components.zha_dynamic_scenes.models import Lamp, LampKind

from .common import FakeTransport, lamp

INTEGRATION = "custom_components.zha_dynamic_scenes"
GROUP = 0xDD92


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
    """Pretend ZHA runs with one group holding the lamps."""
    hass.config.components.add("zha")
    with (
        patch(f"{INTEGRATION}.async_zha_ready", return_value=True),
        patch(f"{INTEGRATION}.config_flow.async_zha_ready", return_value=True),
        patch(f"{INTEGRATION}.manager.ZhaSceneTransport", return_value=transport),
        patch(f"{INTEGRATION}.manager.async_zone_lamps", return_value=lamps),
        patch(
            f"{INTEGRATION}.manager.async_group_member_ieees",
            return_value={lamp.ieee for lamp in lamps},
        ),
        patch(f"{INTEGRATION}.config_flow.async_zone_lamps", return_value=lamps),
        patch(
            f"{INTEGRATION}.config_flow.async_group_lights",
            # ZHA hands out zigpy's group id type, an int that prints as hex
            return_value={"light.zone_group": Group(GROUP)},
        ),
        patch(f"{INTEGRATION}.config_flow.async_group_name", return_value="Zone group"),
        patch(f"{INTEGRATION}.async_listen_scene_losses", return_value=lambda: None),
    ):
        yield


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return the config entry with one zone."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_ZONES: [
                {
                    "group_id": GROUP,
                    "entity_id": "light.zone_group",
                    "clusters": [],
                    "palettes": None,
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    return entry
