"""Helpers shared by the tests."""

from custom_components.zha_dynamic_scenes.engine import TransportError
from custom_components.zha_dynamic_scenes.models import (
    ColorTemperature,
    Lamp,
    LampKind,
    LightAction,
    Palette,
    PaletteColor,
    PaletteSource,
)
from custom_components.zha_dynamic_scenes.protocol import STATUS_SUCCESS

RED = PaletteColor(0.68, 0.31, 80.0)
GREEN = PaletteColor(0.17, 0.70, 70.0)
BLUE = PaletteColor(0.15, 0.06, 60.0)


def lamp(n: int, kind: LampKind = LampKind.DYNAMIC) -> Lamp:
    """Return lamp number n; entity ids sort in lamp order."""
    return Lamp(
        ieee=f"00:17:88:01:00:00:00:{n:02x}",
        endpoint_id=11,
        entity_id=f"light.lamp_{n:02d}",
        kind=kind,
    )


def palette(
    palette_id: str = "custom:a",
    *,
    actions: dict[str, LightAction] | None = None,
    group_name: str | None = None,
) -> Palette:
    """Return a three colour palette."""
    return Palette(
        palette_id=palette_id,
        name=palette_id,
        source=PaletteSource.HUE if actions is not None else PaletteSource.CUSTOM,
        colors=[RED, GREEN, BLUE],
        speed=0.5,
        color_temperature=ColorTemperature(mirek=400, brightness=50.0),
        group_name=group_name,
        actions=actions or {},
    )


class FakeTransport:
    """Records frames; lamps answer SUCCESS unless told otherwise."""

    def __init__(self) -> None:
        """Initialize the fake."""
        self.requests: list[tuple[str, int, bytes]] = []
        self.groupcasts: list[tuple[int, bytes]] = []
        # ieee -> statuses to answer in turn; None means no response
        self.answers: dict[str, list[int | None]] = {}

    async def async_scene_request(self, lamp: Lamp, command_id: int, payload: bytes) -> int:
        """Record a unicast and answer it."""
        self.requests.append((lamp.ieee, command_id, payload))
        if answers := self.answers.get(lamp.ieee):
            if (status := answers.pop(0)) is None:
                raise TransportError("no response")
            return status
        return STATUS_SUCCESS

    async def async_start_dynamic(self, group_id: int, payload: bytes) -> None:
        """Record a groupcast."""
        self.groupcasts.append((group_id, payload))
