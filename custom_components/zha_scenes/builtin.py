"""The built-in palettes shipped with the integration, and their names."""

from __future__ import annotations

from functools import cache
import json
from pathlib import Path

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN
from .models import Palette, PaletteSource

PALETTES_FILE = Path(__file__).parent / "palettes.json"


async def async_builtin_palettes(hass: HomeAssistant) -> dict[str, Palette]:
    """Return the built-in palettes by id."""
    return await hass.async_add_executor_job(_load_palettes)


async def async_palette_names(hass: HomeAssistant, language: str) -> dict[str, str]:
    """
    Return the names of the built-in palettes in a language, by id.

    These are the names of their scene entities; a name missing in the language is English.
    """
    translations = await async_get_translations(hass, language, "entity", [DOMAIN])
    prefix = f"component.{DOMAIN}.entity.{Platform.SCENE}."
    return {
        key.removeprefix(prefix).removesuffix(".name"): name
        for key, name in translations.items()
        if key.startswith(prefix) and key.endswith(".name")
    }


@cache
def _load_palettes() -> dict[str, Palette]:
    """Read the built-in palettes, once."""
    return {
        item["palette_id"]: Palette.from_dict({**item, "source": PaletteSource.BUILTIN})
        for item in json.loads(PALETTES_FILE.read_text(encoding="utf-8"))
    }
