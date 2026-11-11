"""The built-in palettes, the standard scenes and their names."""

import json

from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from custom_components.zha_scenes.builtin import (
    PALETTES_FILE,
    STANDARD_SCENES,
    async_builtin_palettes,
    async_scene_names,
)
from custom_components.zha_scenes.const import MIN_PALETTE_COLORS
from custom_components.zha_scenes.models import STANDARD_SCENE_KEYS, PaletteSource
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


def test_standard_scenes() -> None:
    assert [
        (scene.scene_id, scene.brightness, scene.mireds) for scene in STANDARD_SCENES.values()
    ] == [
        (10, 60, 370),
        (11, 56.25, 447),
        (12, 100, 346),
        (13, 100, 370),
        (14, 10, 500),
        (15, 100, 233),
        (16, 100, 156),
        (17, 25, 500),
        (18, 100, 250),
    ]
    assert all(key == scene.key for key, scene in STANDARD_SCENES.items())
    # the keys zones offer by default
    assert tuple(STANDARD_SCENES) == STANDARD_SCENE_KEYS


def test_every_scene_is_named_in_each_language() -> None:
    names = {item["palette_id"]: item["name"] for item in json.loads(PALETTES_FILE.read_text())}
    # the keys share the scene entities' unique ids and translations with the palette ids
    assert not STANDARD_SCENES.keys() & names.keys()
    for language in ("en", "nl"):
        scenes = json.loads((TRANSLATIONS / f"{language}.json").read_text())["entity"]["scene"]
        assert scenes.keys() == names.keys() | STANDARD_SCENES.keys()
        # zones list their scenes by name
        assert len({scene["name"].casefold() for scene in scenes.values()}) == len(scenes)
    english = json.loads((TRANSLATIONS / "en.json").read_text())["entity"]["scene"]
    assert {palette_id: english[palette_id]["name"] for palette_id in names} == names


async def test_scene_names(hass: HomeAssistant) -> None:
    dutch = await async_scene_names(hass, "nl")
    assert len(dutch) == 53
    assert dutch["rolling_hills"] == "Glooiende heuvels"
    assert dutch["babys_breath"] == "Gipskruid"
    assert dutch["cool_bright"] == "Koel helder"
    # a language without translations gets the English names
    assert (await async_scene_names(hass, "de"))["rolling_hills"] == "Rolling hills"
