"""Frames and reads through ZHA's zigpy application, with zigpy's own response parsing."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest
from zigpy.types import EUI64
from zigpy.zcl import foundation
from zigpy.zcl.clusters.general import Scenes

from custom_components.zha_dynamic_scenes.engine import TransportError
from custom_components.zha_dynamic_scenes.protocol import (
    ATTR_HUE_FEATURES,
    CLUSTER_HUE_EFFECTS,
    CLUSTER_SCENES,
    CMD_ADD_HUE_SCENE,
    MANUFACTURER_SIGNIFY,
)
from custom_components.zha_dynamic_scenes.zigbee import ZhaSceneTransport

from .common import lamp
from .conftest import INTEGRATION


@pytest.fixture
def endpoint() -> Generator[MagicMock]:
    """Return the zigpy endpoint of lamp 1 behind a fake ZHA gateway; its tsn is 0x2a."""
    endpoint = MagicMock()
    device = MagicMock()
    device.device.get_sequence.return_value = 0x2A
    device.device.endpoints = {lamp(1).endpoint_id: endpoint}
    proxy = MagicMock()
    proxy.gateway.devices = {EUI64.convert(lamp(1).ieee): device}
    with patch(f"{INTEGRATION}.zigbee.get_zha_gateway_proxy", return_value=proxy):
        yield endpoint


@pytest.mark.parametrize("status", [0x00, 0x89])
async def test_signify_add_scene(hass: HomeAssistant, endpoint: MagicMock, status: int) -> None:
    # how a Signe answered the bridge, parsed by zigpy's Scenes cluster
    _, response = Scenes(MagicMock()).deserialize(bytes.fromhex(f"1d0b10e402{status:02x}a8f71e"))
    endpoint.request = AsyncMock(return_value=response)

    result = await ZhaSceneTransport(hass).async_scene_request(
        lamp(1), CMD_ADD_HUE_SCENE, b"\x01\x02", manufacturer=MANUFACTURER_SIGNIFY
    )

    assert result == status
    endpoint.request.assert_awaited_once_with(
        CLUSTER_SCENES, 0x2A, bytes.fromhex("050b102a020102"), command_id=CMD_ADD_HUE_SCENE
    )


async def test_read_features(hass: HomeAssistant, endpoint: MagicMock) -> None:
    cluster = endpoint.in_clusters[CLUSTER_HUE_EFFECTS]
    response, _ = foundation.ReadAttributesResponse.deserialize(bytes.fromhex("0100001b2f000000"))
    cluster.read_attributes_raw = AsyncMock(return_value=response)

    assert await ZhaSceneTransport(hass).async_read_features(lamp(1)) == 0x2F
    cluster.read_attributes_raw.assert_awaited_once_with(
        [ATTR_HUE_FEATURES], manufacturer=MANUFACTURER_SIGNIFY
    )


@pytest.mark.parametrize(
    "response",
    [
        # unsupported attribute
        foundation.ReadAttributesResponse.deserialize(bytes.fromhex("010086"))[0],
        # read rejected: unsupported manufacturer general command
        foundation.DefaultResponse(
            command_id=0x00, status=foundation.Status.UNSUP_MANUF_GENERAL_COMMAND
        ),
    ],
)
async def test_read_features_without_bitmap(
    hass: HomeAssistant, endpoint: MagicMock, response: object
) -> None:
    endpoint.in_clusters[CLUSTER_HUE_EFFECTS].read_attributes_raw = AsyncMock(return_value=response)
    with pytest.raises(TransportError):
        await ZhaSceneTransport(hass).async_read_features(lamp(1))
