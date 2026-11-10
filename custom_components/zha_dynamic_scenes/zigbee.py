"""Zone lamps and Zigbee frames through ZHA's zigpy application."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.components.zha.helpers import (
    ZHAGatewayProxy,
    get_zha_gateway_proxy,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from zha.application.const import (
    ATTR_IEEE,
    ATTR_TYPE,
    ZHA_GW_MSG,
    ZHA_GW_MSG_DEVICE_INFO,
    ZHA_GW_MSG_DEVICE_JOINED,
    ZHA_GW_MSG_DEVICE_REMOVED,
)
from zha.application.platforms.light import BaseLight
from zha.application.platforms.light.const import ColorMode
from zigpy.exceptions import ZigbeeException
from zigpy.profiles import zha as zha_profile
from zigpy.types import EUI64
from zigpy.zcl.foundation import ReadAttributesResponse, Status

from .const import DOMAIN
from .engine import TransportError
from .models import Lamp, LampKind
from .protocol import (
    ATTR_HUE_FEATURES,
    CLUSTER_HUE_EFFECTS,
    CLUSTER_SCENES,
    MANUFACTURER_SIGNIFY,
    dynamic_palette_frame,
    zcl_frame,
)

if TYPE_CHECKING:
    from zha.zigbee.group import Group, GroupMember

# manufacturer names of Signify lamps, as ZHA matches them for its Hue light
SIGNIFY_MANUFACTURERS = frozenset({"Philips", "Signify Netherlands B.V."})


class ZhaSceneTransport:
    """Sends Scenes cluster frames through ZHA's zigpy application controller."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the transport."""
        self._hass = hass

    async def async_scene_request(
        self, lamp: Lamp, command_id: int, payload: bytes, *, manufacturer: int | None = None
    ) -> int:
        """
        Send a Scenes cluster command to one lamp.

        :param manufacturer: Manufacturer code of a manufacturer specific command.
        :return: ZCL status of the lamp's response.
        :raises TransportError: The lamp did not answer.
        """
        device = _gateway_proxy(self._hass).gateway.devices[EUI64.convert(lamp.ieee)]
        tsn = device.device.get_sequence()
        endpoint = device.device.endpoints[lamp.endpoint_id]
        try:
            response = await endpoint.request(
                CLUSTER_SCENES,
                tsn,
                zcl_frame(tsn, command_id, payload, manufacturer=manufacturer),
                command_id=command_id,
            )
        except (ZigbeeException, TimeoutError) as err:
            raise TransportError(str(err) or type(err).__name__) from err
        # the scene response or a default response, both carry a status; zigpy parses the
        # Signify Add Scene response as Remove Scene Response, which has the same layout
        return int(response.status)

    async def async_read_features(self, lamp: Lamp) -> int:
        """
        Read the Hue features bitmap of one lamp.

        :raises TransportError: The lamp did not answer or did not report the bitmap.
        """
        device = _gateway_proxy(self._hass).gateway.devices[EUI64.convert(lamp.ieee)]
        cluster = device.device.endpoints[lamp.endpoint_id].in_clusters[CLUSTER_HUE_EFFECTS]
        try:
            response = await cluster.read_attributes_raw(
                [ATTR_HUE_FEATURES], manufacturer=MANUFACTURER_SIGNIFY
            )
        except (ZigbeeException, TimeoutError) as err:
            raise TransportError(str(err) or type(err).__name__) from err
        # a lamp that rejects the read answers with a default response instead
        records = response.status_records if isinstance(response, ReadAttributesResponse) else []
        if not records or records[0].status != Status.SUCCESS:
            raise TransportError(f"no features in {response}")
        return int(records[0].value.value)

    async def async_start_dynamic(self, group_id: int, payload: bytes) -> None:
        """
        Groupcast the dynamic palette command to a Zigbee group.

        :raises TransportError: The frame could not be sent.
        """
        group = _gateway_proxy(self._hass).gateway.groups[group_id].zigpy_group
        tsn = group.get_sequence()
        try:
            await group.request(  # type: ignore[no-untyped-call]
                zha_profile.PROFILE_ID,
                CLUSTER_SCENES,
                tsn,
                dynamic_palette_frame(tsn, payload),
            )
        except (ZigbeeException, TimeoutError) as err:
            raise TransportError(str(err) or type(err).__name__) from err


@callback
def async_zha_ready(hass: HomeAssistant) -> bool:
    """Return whether ZHA's gateway is running."""
    try:
        get_zha_gateway_proxy(hass)
    except ValueError:
        return False
    return True


@callback
def async_group_lights(hass: HomeAssistant) -> dict[str, int]:
    """Return the ZHA group light entity ids with their Zigbee group id."""
    return {
        # a plain int: zigpy's group id type prints as hex, which would leak into storage keys
        ref.ha_entity_id: int(ref.entity_data.group_proxy.group.group_id)
        for refs in _gateway_proxy(hass).ha_entity_refs.values()
        for ref in refs
        if ref.entity_data.group_proxy is not None
        and ref.entity_data.is_group_entity
        and isinstance(ref.entity_data.entity, BaseLight)
    }


@callback
def async_group_member_ieees(hass: HomeAssistant, group_id: int) -> set[str]:
    """
    Return the IEEE addresses of the devices in a ZHA group.

    :raises HomeAssistantError: ZHA is not loaded or the group does not exist.
    """
    return {str(member.device.ieee) for member in _group(_gateway_proxy(hass), group_id).members}


@callback
def async_zone_lamps(hass: HomeAssistant, group_id: int) -> list[Lamp]:
    """
    Return the available lights in a ZHA group.

    :raises HomeAssistantError: ZHA is not loaded or the group does not exist.
    """
    proxy = _gateway_proxy(hass)
    lamps = []
    for member in _group(proxy, group_id).members:
        if not member.device.available:
            continue
        light = next((e for e in member.associated_entities if isinstance(e, BaseLight)), None)
        if light is None:
            continue
        refs = proxy.ha_entity_refs.get(member.device.ieee, [])
        entity_id = next(
            (ref.ha_entity_id for ref in refs if ref.entity_data.entity is light), None
        )
        if entity_id is None:
            continue
        lamps.append(
            Lamp(
                ieee=str(member.device.ieee),
                endpoint_id=member.endpoint_id,
                entity_id=entity_id,
                kind=_lamp_kind(member, light),
            )
        )
    return lamps


@callback
def async_group_name(hass: HomeAssistant, group_id: int) -> str | None:
    """Return the name of a ZHA group."""
    group = _gateway_proxy(hass).gateway.groups.get(group_id)
    return group.name if group else None


@callback
def async_listen_scene_losses(
    hass: HomeAssistant, forget_lamp: Callable[[str], None]
) -> CALLBACK_TYPE:
    """
    Report devices that may have lost their stored scenes.

    :param forget_lamp: Called with the IEEE of a device that joined with a new address
        (a reset lamp gets one) or was removed from ZHA. A power cycle keeps the address
        and the scenes.
    """

    @callback
    def _handle(message: dict[str, Any]) -> None:
        if message[ATTR_TYPE] in (ZHA_GW_MSG_DEVICE_JOINED, ZHA_GW_MSG_DEVICE_REMOVED):
            forget_lamp(message[ZHA_GW_MSG_DEVICE_INFO][ATTR_IEEE])

    return async_dispatcher_connect(hass, ZHA_GW_MSG, _handle)


def _gateway_proxy(hass: HomeAssistant) -> ZHAGatewayProxy:
    """Return ZHA's gateway proxy."""
    try:
        return get_zha_gateway_proxy(hass)
    except ValueError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="zha_not_ready"
        ) from err


def _group(proxy: ZHAGatewayProxy, group_id: int) -> Group:
    """Return a ZHA group."""
    if (group := proxy.gateway.groups.get(group_id)) is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="group_not_found",
            translation_placeholders={"group": f"0x{group_id:04x}"},
        )
    return group


def _lamp_kind(member: GroupMember, light: BaseLight) -> LampKind:
    """Return what a group member can do, from its colour modes and Hue clusters."""
    if ColorMode.XY in light.supported_color_modes:
        endpoint = member.device.device.endpoints[member.endpoint_id]
        if (
            member.device.manufacturer in SIGNIFY_MANUFACTURERS
            and CLUSTER_HUE_EFFECTS in endpoint.in_clusters
        ):
            return LampKind.DYNAMIC
        return LampKind.COLOR
    if ColorMode.COLOR_TEMP in light.supported_color_modes:
        return LampKind.COLOR_TEMP
    return LampKind.PLAIN
