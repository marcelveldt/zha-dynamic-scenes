"""Constants for ZHA Scenes."""

from typing import Final

from homeassistant.core import Context
from homeassistant.util.signal_type import SignalTypeFormat

DOMAIN: Final = "zha_scenes"

CONF_ZONES: Final = "zones"

ATTR_SPEED: Final = "speed"

SERVICE_ACTIVATE: Final = "activate"
SERVICE_REWRITE_SCENES: Final = "rewrite_scenes"

SIGNAL_LIBRARY_UPDATED: Final = f"{DOMAIN}_library_updated"
# per zone (zone id), with the id of the started palette and the context that started it
SIGNAL_ZONE_ACTIVATED: Final = SignalTypeFormat[str, Context | None](
    f"{DOMAIN}_zone_activated_{{}}"
)

STORAGE_VERSION: Final = 1

DEFAULT_SPEED: Final = 0.627  # speed of the Hue gallery scenes
MIN_PALETTE_COLORS: Final = 2  # a single colour has nothing to animate
