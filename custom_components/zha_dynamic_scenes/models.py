"""Data models for palettes, zones and lamps."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class PaletteSource(StrEnum):
    """Where a palette comes from."""

    HUE = "hue"
    CUSTOM = "custom"


class LampKind(StrEnum):
    """What a lamp in a zone can do with a palette."""

    DYNAMIC = "dynamic"  # Signify colour lamp that animates the palette itself
    COLOR = "color"
    COLOR_TEMP = "color_temp"
    PLAIN = "plain"


@dataclass(frozen=True, slots=True)
class PaletteColor:
    """
    A palette colour.

    :param x: CIE 1931 x.
    :param y: CIE 1931 y.
    :param brightness: Brightness in percent (0..100).
    """

    x: float
    y: float
    brightness: float

    @property
    def xy(self) -> tuple[float, float]:
        """Return the colour as an xy tuple."""
        return (self.x, self.y)


@dataclass(frozen=True, slots=True)
class ColorTemperature:
    """Colour temperature entry of a palette, for lamps without colour."""

    mirek: int
    brightness: float


@dataclass(frozen=True, slots=True)
class LightAction:
    """
    State a Hue scene sets on one lamp.

    :param xy: Colour, None when the lamp is set to a colour temperature.
    :param mirek: Colour temperature, None when the lamp is set to a colour.
    :param brightness: Brightness in percent, None when the scene keeps it.
    """

    xy: tuple[float, float] | None = None
    mirek: int | None = None
    brightness: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LightAction:
        """Create from the stored dict."""
        xy = data.get("xy")
        return cls(
            xy=(xy[0], xy[1]) if xy else None,
            mirek=data.get("mirek"),
            brightness=data.get("brightness"),
        )


@dataclass(slots=True)
class Palette:
    """
    A dynamic scene palette.

    :param palette_id: Stable id, unique in the library.
    :param name: Display name.
    :param source: Hue import or custom.
    :param colors: Colours the lamps animate through, in order.
    :param speed: Animation speed 0..1.
    :param color_temperature: Colour temperature for lamps without colour.
    :param group_name: Name of the Hue room or zone the scene came from.
    :param actions: Per-lamp start states of an imported Hue scene, keyed by IEEE.
    """

    palette_id: str
    name: str
    source: PaletteSource
    colors: list[PaletteColor]
    speed: float
    color_temperature: ColorTemperature | None = None
    group_name: str | None = None
    actions: dict[str, LightAction] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """Return the name with its origin, to tell equally named palettes apart."""
        return f"{self.name} ({self.group_name})" if self.group_name else self.name

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serializable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Palette:
        """Create from the stored dict."""
        color_temperature = data.get("color_temperature")
        return cls(
            palette_id=data["palette_id"],
            name=data["name"],
            source=PaletteSource(data["source"]),
            colors=[PaletteColor(**color) for color in data["colors"]],
            speed=data["speed"],
            color_temperature=(
                ColorTemperature(**color_temperature) if color_temperature else None
            ),
            group_name=data.get("group_name"),
            actions={
                ieee: LightAction.from_dict(action)
                for ieee, action in data.get("actions", {}).items()
            },
        )


@dataclass(slots=True)
class ZoneConfig:
    """
    A zone: a ZHA group whose lamps play palettes together.

    :param group_id: Zigbee group id of the ZHA group.
    :param entity_id: Entity registry id of the ZHA group light.
    :param clusters: Lists of lamp IEEEs that share a start colour.
    :param palettes: Ids of the palettes offered in the zone, None for the automatic choice.
    """

    group_id: int
    entity_id: str
    clusters: list[list[str]] = field(default_factory=list)
    palettes: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serializable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ZoneConfig:
        """Create from the stored dict."""
        return cls(
            group_id=int(data["group_id"]),
            entity_id=data["entity_id"],
            clusters=[list(cluster) for cluster in data.get("clusters", [])],
            palettes=data.get("palettes"),
        )


@dataclass(frozen=True, slots=True)
class Lamp:
    """
    A light in a zone.

    :param ieee: IEEE address as ZHA prints it.
    :param endpoint_id: Endpoint that is a member of the zone's group.
    :param entity_id: Home Assistant light entity.
    :param kind: What the lamp can do with a palette.
    """

    ieee: str
    endpoint_id: int
    entity_id: str
    kind: LampKind
