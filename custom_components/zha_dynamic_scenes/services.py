"""Services of ZHA dynamic scenes."""

from __future__ import annotations

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_TRANSITION
from homeassistant.components.scene import DOMAIN as SCENE_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import service
import probatio

from .const import ATTR_SPEED, DOMAIN, SERVICE_ACTIVATE, SERVICE_REWRITE_SCENES


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register `activate` for the palette scenes and `rewrite_scenes` for the zone selects."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_ACTIVATE,
        entity_domain=SCENE_DOMAIN,
        schema={
            probatio.Optional(ATTR_SPEED): probatio.All(
                probatio.Coerce(float), probatio.Range(min=0, max=1)
            ),
            probatio.Optional(ATTR_BRIGHTNESS): probatio.All(
                probatio.Coerce(int), probatio.Range(min=1, max=255)
            ),
            probatio.Optional(ATTR_TRANSITION): probatio.All(
                probatio.Coerce(float), probatio.Range(min=0, max=6553)
            ),
        },
        # the entry point of `scene.turn_on`, which records the activation
        func="_async_activate",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_REWRITE_SCENES,
        entity_domain=SELECT_DOMAIN,
        schema=None,
        func="async_rewrite_scenes",
    )
