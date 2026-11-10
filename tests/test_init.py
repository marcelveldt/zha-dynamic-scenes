"""Setup, the zone select entity and the services."""

from unittest.mock import patch

from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_mock_service

from custom_components.zha_dynamic_scenes.const import CONF_ZONES, DOMAIN
from custom_components.zha_dynamic_scenes.zigbee import async_listen_scene_losses

from .common import FakeTransport, lamp, palette
from .conftest import GROUP, INTEGRATION

ENTITY_ID = "select.dynamic_scene"


@pytest.fixture
async def loaded(hass: HomeAssistant, entry: MockConfigEntry, zha: None) -> MockConfigEntry:
    """Set up the entry with two palettes in the library."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    entry.runtime_data.async_update_palettes([palette("custom:a"), palette("custom:b")])
    await hass.async_block_till_done()
    return entry


async def test_select_lists_and_starts_palettes(
    hass: HomeAssistant, loaded: MockConfigEntry, transport: FakeTransport
) -> None:
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["options"] == ["custom:a", "custom:b"]
    async_mock_service(hass, "light", "turn_on")

    await hass.services.async_call(
        "select",
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_OPTION: "custom:b"},
        blocking=True,
    )
    assert hass.states.get(ENTITY_ID).state == "custom:b"
    assert len(transport.groupcasts) == 1


async def test_activate_service(
    hass: HomeAssistant, loaded: MockConfigEntry, transport: FakeTransport
) -> None:
    async_mock_service(hass, "light", "turn_on")
    await hass.services.async_call(
        DOMAIN,
        "activate",
        {ATTR_ENTITY_ID: ENTITY_ID, "palette": "custom:a", "speed": 0.25, "brightness": 50},
        blocking=True,
    )
    assert hass.states.get(ENTITY_ID).state == "custom:a"
    payload = transport.groupcasts[0][1]
    assert payload[3] == 0x0E  # flags: brightness, palette, speed
    assert payload[4] == 50
    assert payload[-1] == round(0.25 * 255)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "activate",
            {ATTR_ENTITY_ID: ENTITY_ID, "palette": "nope"},
            blocking=True,
        )


async def test_rewrite_scenes_service(
    hass: HomeAssistant, loaded: MockConfigEntry, transport: FakeTransport
) -> None:
    async_mock_service(hass, "light", "turn_on")
    activate = {ATTR_ENTITY_ID: ENTITY_ID, "palette": "custom:a"}
    await hass.services.async_call(DOMAIN, "activate", activate, blocking=True)
    await hass.services.async_call(DOMAIN, "activate", activate, blocking=True)
    assert len(transport.requests) == 2

    await hass.services.async_call(
        DOMAIN, "rewrite_scenes", {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    await hass.services.async_call(DOMAIN, "activate", activate, blocking=True)
    assert len(transport.requests) == 4


async def test_last_palette_is_restored(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    async_mock_service(hass, "light", "turn_on")
    await hass.services.async_call(
        DOMAIN, "activate", {ATTR_ENTITY_ID: ENTITY_ID, "palette": "custom:b"}, blocking=True
    )
    assert await hass.config_entries.async_reload(loaded.entry_id)
    await hass.async_block_till_done()
    assert loaded.state is ConfigEntryState.LOADED
    assert hass.states.get(ENTITY_ID).state == "custom:b"


async def test_setup_retries_without_zha(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.config.components.add("zha")
    with patch(f"{INTEGRATION}.async_zha_ready", return_value=False):
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_scene_loss_signals(hass: HomeAssistant) -> None:
    lamps: list[str] = []
    unsub = async_listen_scene_losses(hass, lamps.append)
    for message_type in ("device_joined", "device_removed", "device_fully_initialized"):
        async_dispatcher_send(
            hass,
            "zha_gateway_message",
            {"type": message_type, "device_info": {"ieee": lamp(1).ieee}},
        )
    async_dispatcher_send(
        hass,
        "zha_gateway_message",
        {"type": "group_member_removed", "group_info": {"group_id": GROUP, "members": []}},
    )
    unsub()
    assert lamps == [lamp(1).ieee, lamp(1).ieee]


async def test_deleted_zone_removes_its_entity(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    assert registry.async_get(ENTITY_ID) is not None
    hass.config_entries.async_update_entry(loaded, options={CONF_ZONES: []})
    assert await hass.config_entries.async_reload(loaded.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(ENTITY_ID) is None
