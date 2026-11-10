"""
ZCL frames for Hue dynamic palettes and the scenes they start from.

Pure encoding, no Home Assistant or zigpy imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import struct

MANUFACTURER_SIGNIFY = 0x100B
CLUSTER_ON_OFF = 0x0006
CLUSTER_LEVEL = 0x0008
CLUSTER_SCENES = 0x0005
CLUSTER_COLOR = 0x0300
CLUSTER_HUE_EFFECTS = 0xFC03

CMD_REMOVE_SCENE = 0x02
CMD_ENHANCED_ADD_SCENE = 0x40
CMD_DYNAMIC_PALETTE = 0x00  # manufacturer specific, same id as Add Scene

STATUS_SUCCESS = 0x00
STATUS_INSUFFICIENT_SPACE = 0x89

MAX_PALETTE_COLORS = 9
MAX_LEVEL = 254

_FC_CLUSTER_SPECIFIC = 0x01
_FC_MANUFACTURER_SPECIFIC = 0x04
_FC_DISABLE_DEFAULT_RESPONSE = 0x10

_FLAG_TRANSITION = 0x01
_FLAG_BRIGHTNESS = 0x02
_FLAG_PALETTE = 0x04
_FLAG_SPEED = 0x08

# palette xy is packed in 12 bits per axis, scaled to these maxima (fitted on Hue bridge frames)
_PALETTE_MAX_X = 0.7347
_PALETTE_MAX_Y = 0.8341
_ZCL_MAX_XY = 0xFEFF


@dataclass(frozen=True, slots=True)
class SceneContent:
    """
    State a lamp stores for a scene: on, level and colour.

    :param level: Level 1..254.
    :param x: ZCL CurrentX (CIE x * 65536).
    :param y: ZCL CurrentY (CIE y * 65536).
    """

    level: int
    x: int
    y: int

    @classmethod
    def from_xy(cls, xy: tuple[float, float], brightness: float) -> SceneContent:
        """
        Build the content from CIE xy and a Hue style brightness percentage.

        :param xy: CIE 1931 colour.
        :param brightness: Brightness in percent (0..100); 0 is the lowest level, not off.
        """
        return cls(
            # the Hue bridge maps 20% to 0x34 and 50% to 0x80
            level=max(1, min(MAX_LEVEL, round(brightness * MAX_LEVEL / 100) + 1)),
            x=min(_ZCL_MAX_XY, round(xy[0] * 65536)),
            y=min(_ZCL_MAX_XY, round(xy[1] * 65536)),
        )


def zcl_frame(
    tsn: int,
    command_id: int,
    payload: bytes,
    *,
    manufacturer: int | None = None,
    disable_default_response: bool = False,
) -> bytes:
    """Return a cluster specific client-to-server ZCL frame."""
    frame_control = _FC_CLUSTER_SPECIFIC
    if manufacturer is not None:
        frame_control |= _FC_MANUFACTURER_SPECIFIC
    if disable_default_response:
        frame_control |= _FC_DISABLE_DEFAULT_RESPONSE
    header = bytes([frame_control])
    if manufacturer is not None:
        header += struct.pack("<H", manufacturer)
    return header + bytes([tsn, command_id]) + payload


def dynamic_palette_frame(tsn: int, payload: bytes) -> bytes:
    """Return the Signify Scenes frame that starts a dynamic palette."""
    return zcl_frame(
        tsn,
        CMD_DYNAMIC_PALETTE,
        payload,
        manufacturer=MANUFACTURER_SIGNIFY,
        disable_default_response=True,
    )


def dynamic_palette_payload(
    group_id: int,
    scene_id: int,
    colors: Sequence[tuple[float, float]],
    *,
    speed: float | None = None,
    brightness: int | None = None,
    transition: int | None = None,
) -> bytes:
    """
    Return the payload of the dynamic palette command.

    The lamps first apply the stored scene (group_id, scene_id), then animate the palette
    starting at the palette colour nearest to the stored colour.

    :param group_id: Group of the stored scene.
    :param scene_id: Id of the stored scene.
    :param colors: Palette colours as CIE xy, at most 9.
    :param speed: Animation speed 0..1.
    :param brightness: Level 1..254 for all lamps instead of the stored level.
    :param transition: Transition time in 1/10 s.
    """
    if not 0 < len(colors) <= MAX_PALETTE_COLORS:
        msg = f"a palette needs 1..{MAX_PALETTE_COLORS} colours, got {len(colors)}"
        raise ValueError(msg)
    flags, fields = _FLAG_PALETTE, b""
    if transition is not None:
        flags |= _FLAG_TRANSITION
        fields += struct.pack("<H", transition)
    if brightness is not None:
        flags |= _FLAG_BRIGHTNESS
        fields += bytes([min(MAX_LEVEL, brightness)])
    block = bytes([len(colors) << 4, 0x00, 0x00, 0x00]) + b"".join(
        pack_palette_xy(*xy) for xy in colors
    )
    fields += bytes([len(block)]) + block
    if speed is not None:
        flags |= _FLAG_SPEED
        fields += bytes([min(255, round(speed * 255))])
    return struct.pack("<HBB", group_id, scene_id, flags) + fields


def pack_palette_xy(x: float, y: float) -> bytes:
    """Return a palette colour in the 3-byte, 12 bits per axis encoding."""
    qx = min(0xFFF, int(x * 0xFFF / _PALETTE_MAX_X))
    qy = min(0xFFF, int(y * 0xFFF / _PALETTE_MAX_Y))
    return bytes([qx & 0xFF, ((qx >> 8) & 0x0F) | ((qy & 0x0F) << 4), (qy >> 4) & 0xFF])


def add_scene_payload(
    group_id: int, scene_id: int, content: SceneContent, transition: int
) -> bytes:
    """
    Return the payload of an Enhanced Add Scene with on, level and colour.

    :param transition: Transition time in 1/10 s used when the scene is applied.
    """
    return (
        struct.pack("<HBHB", group_id, scene_id, transition, 0)  # empty scene name
        + struct.pack("<HBB", CLUSTER_ON_OFF, 1, 1)
        + struct.pack("<HBB", CLUSTER_LEVEL, 1, content.level)
        + struct.pack("<HBHH", CLUSTER_COLOR, 4, content.x, content.y)
    )


def remove_scene_payload(group_id: int, scene_id: int) -> bytes:
    """Return the payload of a standard Remove Scene."""
    return struct.pack("<HB", group_id, scene_id)
