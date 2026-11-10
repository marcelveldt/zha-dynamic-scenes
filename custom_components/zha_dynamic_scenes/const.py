"""Constants for ZHA dynamic scenes."""

from typing import Final

DOMAIN: Final = "zha_dynamic_scenes"

CONF_ZONES: Final = "zones"
CONF_HUE_HOST: Final = "hue_host"
CONF_HUE_APP_KEY: Final = "hue_app_key"

ATTR_PALETTE: Final = "palette"
ATTR_SPEED: Final = "speed"

SERVICE_ACTIVATE: Final = "activate"
SERVICE_REWRITE_SCENES: Final = "rewrite_scenes"

SIGNAL_LIBRARY_UPDATED: Final = f"{DOMAIN}_library_updated"

STORAGE_VERSION: Final = 1

DEFAULT_SPEED: Final = 0.627  # speed of the Hue gallery scenes
MIN_PALETTE_COLORS: Final = 2  # a single colour has nothing to animate
