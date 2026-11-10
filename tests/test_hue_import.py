"""Import of palettes from a Hue bridge resource listing."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.zha_dynamic_scenes.hue_import import (
    HueAuthError,
    HueImportError,
    async_fetch_palettes,
    parse_palettes,
)
from custom_components.zha_dynamic_scenes.models import (
    ColorTemperature,
    Gradient,
    GradientMode,
    LightAction,
    Palette,
    PaletteColor,
    PaletteSource,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hue_resources.json"
IEEE_1 = "00:17:88:01:00:00:00:01"
IEEE_2 = "00:17:88:01:00:00:00:02"
IEEE_3 = "00:17:88:01:00:00:00:03"


def _resources() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parse_palettes() -> None:
    palettes = {p.palette_id: p for p in parse_palettes(_resources()["data"])}
    # single colour and colourless scenes are not dynamic palettes
    assert set(palettes) == {"hue:scene-dynamic", "hue:scene-gradient"}
    assert palettes["hue:scene-dynamic"] == Palette(
        palette_id="hue:scene-dynamic",
        name="Sunset",
        source=PaletteSource.HUE,
        colors=[
            PaletteColor(0.6, 0.35, 80.0),
            PaletteColor(0.5, 0.41, 80.0),
            PaletteColor(0.45, 0.4, 75.0),
        ],
        speed=0.6269841,
        color_temperature=ColorTemperature(mirek=400, brightness=80.0),
        group_name="Ambience",
        # light-4 has no Zigbee connectivity, so no IEEE
        actions={
            IEEE_2: LightAction(xy=(0.5, 0.4), brightness=40.0),
            IEEE_3: LightAction(mirek=366, brightness=60.0),
        },
    )
    gradient = palettes["hue:scene-gradient"]
    assert gradient.group_name == "Living room"
    assert gradient.color_temperature is None
    assert gradient.actions == {
        IEEE_1: LightAction(
            xy=(0.2, 0.5),
            brightness=90.0,
            gradient=Gradient(
                points=((0.2, 0.5), (0.3, 0.2), (0.4, 0.4)), mode=GradientMode.SEGMENTED_PALETTE
            ),
        ),
        IEEE_2: LightAction(),
    }
    assert gradient.label == "Aurora (Living room)"


def test_palette_survives_storage_round_trip() -> None:
    for palette in parse_palettes(_resources()["data"]):
        assert Palette.from_dict(json.loads(json.dumps(palette.to_dict()))) == palette


async def test_fetch_palettes() -> None:
    session = _session(200, _resources())
    palettes = await async_fetch_palettes(session, "192.0.2.10", "secret")
    assert len(palettes) == 2
    session.get.assert_called_once()
    assert session.get.call_args.args == ("https://192.0.2.10/clip/v2/resource",)
    assert session.get.call_args.kwargs["headers"] == {"hue-application-key": "secret"}


async def test_fetch_palettes_rejected_key() -> None:
    with pytest.raises(HueAuthError):
        await async_fetch_palettes(_session(403, None), "192.0.2.10", "wrong")


async def test_fetch_palettes_unreachable() -> None:
    session = MagicMock()
    session.get.side_effect = aiohttp.ClientConnectionError("refused")
    with pytest.raises(HueImportError):
        await async_fetch_palettes(session, "192.0.2.10", "secret")


def _session(status: int, body: dict | None) -> MagicMock:
    response = MagicMock(status=status)
    response.json = AsyncMock(return_value=body)
    response.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value.__aenter__ = AsyncMock(return_value=response)
    session.get.return_value.__aexit__ = AsyncMock(return_value=None)
    return session
