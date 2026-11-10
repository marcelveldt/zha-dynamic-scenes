"""Helpers shared by the tests."""

from custom_components.zha_scenes.engine import TransportError
from custom_components.zha_scenes.models import (
    ColorTemperature,
    Gradient,
    Lamp,
    LampKind,
    Palette,
    PaletteColor,
    PaletteSource,
)
from custom_components.zha_scenes.protocol import MANUFACTURER_SIGNIFY, STATUS_SUCCESS

RED = PaletteColor(0.68, 0.31, 80.0)
GREEN = PaletteColor(0.17, 0.70, 70.0)
BLUE = PaletteColor(0.15, 0.06, 60.0)
# the gradient a gradient lamp gets from `palette()`
PALETTE_GRADIENT = Gradient(points=(RED.xy, GREEN.xy, BLUE.xy))

# Hue features bitmaps read on ZHA: a Signe gradient and a colour lamp without gradient
FEATURES_GRADIENT = 0x2F
FEATURES_COLOR = 0x07


def lamp(n: int, kind: LampKind = LampKind.DYNAMIC) -> Lamp:
    """Return lamp number n; entity ids sort in lamp order."""
    return Lamp(
        ieee=f"00:17:88:01:00:00:00:{n:02x}",
        endpoint_id=11,
        entity_id=f"light.lamp_{n:02d}",
        kind=kind,
    )


def palette(palette_id: str = "custom:a", *, name: str | None = None) -> Palette:
    """Return a three colour custom palette, named like its id unless a name is given."""
    return Palette(
        palette_id=palette_id,
        name=name or palette_id,
        source=PaletteSource.CUSTOM,
        colors=[RED, GREEN, BLUE],
        speed=0.5,
        color_temperature=ColorTemperature(mirek=400, brightness=50.0),
    )


class FakeTransport:
    """Records frames; lamps answer SUCCESS and have no gradient unless told otherwise."""

    def __init__(self) -> None:
        """Initialize the fake."""
        self.requests: list[tuple[str, int, bytes]] = []
        # manufacturer specific (Signify) unicasts
        self.signify_requests: list[tuple[str, int, bytes]] = []
        self.groupcasts: list[tuple[int, bytes]] = []
        # dynamic palette starts sent to one lamp
        self.unicasts: list[tuple[str, bytes]] = []
        # ieee -> statuses to answer in turn; None means no response
        self.answers: dict[str, list[int | None]] = {}
        # ieee -> Hue features bitmap; None means no response
        self.features: dict[str, int | None] = {}
        self.feature_reads: list[str] = []

    async def async_scene_request(
        self, lamp: Lamp, command_id: int, payload: bytes, *, manufacturer: int | None = None
    ) -> int:
        """Record a unicast and answer it."""
        if manufacturer is None:
            self.requests.append((lamp.ieee, command_id, payload))
        else:
            assert manufacturer == MANUFACTURER_SIGNIFY
            self.signify_requests.append((lamp.ieee, command_id, payload))
        if answers := self.answers.get(lamp.ieee):
            if (status := answers.pop(0)) is None:
                raise TransportError("no response")
            return status
        return STATUS_SUCCESS

    async def async_read_features(self, lamp: Lamp) -> int:
        """Record a features read and answer it."""
        self.feature_reads.append(lamp.ieee)
        if (features := self.features.get(lamp.ieee, FEATURES_COLOR)) is None:
            raise TransportError("no response")
        return features

    async def async_start_dynamic(self, group_id: int, payload: bytes) -> None:
        """Record a groupcast."""
        self.groupcasts.append((group_id, payload))

    async def async_start_dynamic_lamp(self, lamp: Lamp, payload: bytes) -> None:
        """Record a dynamic start sent to one lamp."""
        self.unicasts.append((lamp.ieee, payload))
