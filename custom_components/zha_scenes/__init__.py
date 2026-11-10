"""Hue standard and dynamic scenes on lamps paired to ZHA."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, STORAGE_VERSION
from .manager import ZhaScenesManager
from .services import async_setup_services
from .zigbee import async_listen_scene_losses, async_zha_ready

PLATFORMS = [Platform.SCENE, Platform.SELECT]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ZhaScenesConfigEntry = ConfigEntry[ZhaScenesManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZhaScenesConfigEntry) -> bool:
    """Set up the zones."""
    if not async_zha_ready(hass):
        raise ConfigEntryNotReady(translation_domain=DOMAIN, translation_key="zha_not_ready")
    entry.runtime_data = await ZhaScenesManager.async_load(hass, entry)
    manager = entry.runtime_data
    entry.async_on_unload(
        async_listen_scene_losses(hass, manager.forget_lamp, manager.forget_group)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZhaScenesConfigEntry) -> bool:
    """Unload the zones."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ZhaScenesConfigEntry) -> None:
    """Remove the palette library and the scene cache."""
    await Store[dict[str, Any]](hass, STORAGE_VERSION, DOMAIN).async_remove()
