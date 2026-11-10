"""Import dynamic scene palettes from a Hue bridge (CLIP v2, read only)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import aiohttp

from .const import MIN_PALETTE_COLORS
from .models import ColorTemperature, Palette, PaletteColor, PaletteSource
from .protocol import MAX_PALETTE_COLORS

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class HueImportError(Exception):
    """The bridge could not be read."""


class HueAuthError(HueImportError):
    """The bridge rejected the application key."""


async def async_fetch_palettes(
    session: aiohttp.ClientSession, host: str, app_key: str
) -> list[Palette]:
    """
    Read the dynamic scene palettes of a Hue bridge.

    :param session: Session that accepts the bridge's self-signed certificate.
    :param host: Host name or IP address of the bridge.
    :param app_key: Hue application key (CLIP v2 `hue-application-key`).
    :raises HueAuthError: The key was rejected.
    :raises HueImportError: The bridge could not be reached or read.
    """
    try:
        async with session.get(
            f"https://{host}/clip/v2/resource",
            headers={"hue-application-key": app_key},
            timeout=REQUEST_TIMEOUT,
        ) as response:
            if response.status in (401, 403):
                raise HueAuthError
            response.raise_for_status()
            resources = (await response.json())["data"]
    except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
        raise HueImportError(str(err) or type(err).__name__) from err
    return parse_palettes(resources)


def parse_palettes(resources: list[dict[str, Any]]) -> list[Palette]:
    """
    Return the dynamic scene palettes in a CLIP v2 resource listing.

    Scenes with fewer than two palette colours (Hue's static "Rest" and "Nightlight"
    style scenes) are skipped. A palette lists its lamps by IEEE address, which is the
    same address the lamp has in ZHA.

    :param resources: The `data` list of `GET /clip/v2/resource`.
    """
    by_type: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for resource in resources:
        by_type[resource["type"]].append(resource)
    mac_by_device = {
        item["owner"]["rid"]: item["mac_address"] for item in by_type["zigbee_connectivity"]
    }
    ieee_by_light = {
        light["id"]: mac_by_device[light["owner"]["rid"]]
        for light in by_type["light"]
        if light["owner"]["rid"] in mac_by_device
    }
    group_names = {
        group["id"]: group["metadata"]["name"] for group in by_type["room"] + by_type["zone"]
    }

    palettes = []
    for scene in by_type["scene"]:
        palette = scene.get("palette", {})
        if len(colors := palette.get("color", [])) < MIN_PALETTE_COLORS:
            continue
        color_temperature = next(iter(palette.get("color_temperature", [])), None)
        palettes.append(
            Palette(
                palette_id=f"hue:{scene['id']}",
                name=scene["metadata"]["name"],
                source=PaletteSource.HUE,
                colors=[
                    PaletteColor(
                        x=color["color"]["xy"]["x"],
                        y=color["color"]["xy"]["y"],
                        brightness=color["dimming"]["brightness"],
                    )
                    for color in colors[:MAX_PALETTE_COLORS]
                ],
                speed=scene["speed"],
                color_temperature=ColorTemperature(
                    mirek=color_temperature["color_temperature"]["mirek"],
                    brightness=color_temperature["dimming"]["brightness"],
                )
                if color_temperature
                else None,
                group_name=group_names.get(scene["group"]["rid"]),
                lamps=[
                    ieee_by_light[action["target"]["rid"]]
                    for action in scene["actions"]
                    if action["target"]["rid"] in ieee_by_light
                ],
            )
        )
    return palettes
