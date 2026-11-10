"""The built-in palettes and their names."""

import json

from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from custom_components.zha_scenes.builtin import (
    PALETTES_FILE,
    async_builtin_palettes,
    async_palette_names,
)
from custom_components.zha_scenes.const import MIN_PALETTE_COLORS
from custom_components.zha_scenes.models import PaletteSource
from custom_components.zha_scenes.protocol import MAX_PALETTE_COLORS

TRANSLATIONS = PALETTES_FILE.parent / "translations"


async def test_builtin_palettes(hass: HomeAssistant) -> None:
    palettes = await async_builtin_palettes(hass)

    assert len(palettes) == 44
    for palette_id, palette in palettes.items():
        assert palette.palette_id == palette_id == slugify(palette.name.replace("'", ""))
        assert palette.source is PaletteSource.BUILTIN
        assert MIN_PALETTE_COLORS <= len(palette.colors) <= MAX_PALETTE_COLORS
        assert 0 < palette.speed <= 1
        assert all(0 < color.brightness <= 100 for color in palette.colors)
    assert palettes["babys_breath"].name == "Baby's breath"


def test_every_palette_is_named_in_each_language() -> None:
    names = {item["palette_id"]: item["name"] for item in json.loads(PALETTES_FILE.read_text())}
    for language in ("en", "nl"):
        scenes = json.loads((TRANSLATIONS / f"{language}.json").read_text())["entity"]["scene"]
        assert scenes.keys() == names.keys()
    english = json.loads((TRANSLATIONS / "en.json").read_text())["entity"]["scene"]
    assert {palette_id: scene["name"] for palette_id, scene in english.items()} == names


async def test_palette_names(hass: HomeAssistant) -> None:
    dutch = await async_palette_names(hass, "nl")
    assert len(dutch) == 44
    assert dutch["rolling_hills"] == "Glooiende heuvels"
    assert dutch["babys_breath"] == "Gipskruid"
    # a language without translations gets the English names
    assert (await async_palette_names(hass, "de"))["rolling_hills"] == "Rolling hills"
