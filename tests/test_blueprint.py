"""The Tap Dial automation blueprint."""

from collections.abc import Generator
from pathlib import Path
import shutil
from typing import Any
from unittest.mock import patch

from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_mock_service
from zhaquirks.philips.rdm002 import PhilipsRDM002

PATH = "zha_dynamic_scenes/tap_dial.yaml"
BLUEPRINT = Path(__file__).parents[1] / "blueprints" / "automation" / PATH
IEEE = "00:17:88:01:0b:00:00:01"
# the triggers ZHA offers for the Tap Dial
TRIGGERS = PhilipsRDM002.device_automation_triggers
SELECT = "select.zone_group_dynamic_scene"
SCENES = {f"button_{n}": f"scene.zone_group_palette_{n}" for n in (1, 2, 3)}


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Install the blueprint in the configuration directory."""
    target = Path(hass_tmp_config_dir, "blueprints", "automation", PATH)
    target.parent.mkdir(parents=True)
    shutil.copy(BLUEPRINT, target)
    return hass_tmp_config_dir


@pytest.fixture
def tap_dial(hass: HomeAssistant, device_registry: dr.DeviceRegistry) -> Generator[str]:
    """Register a Tap Dial paired to ZHA and return its device id."""
    hass.config.components.add("zha")
    zha_entry = MockConfigEntry(domain="zha", state=ConfigEntryState.LOADED)
    zha_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=zha_entry.entry_id,
        identifiers={("zha", IEEE)},
        model="RDM002",
    )
    with patch(
        "homeassistant.components.zha.device_trigger._get_device_trigger_data",
        return_value=(IEEE, TRIGGERS),
    ):
        yield device.id


async def test_automation_config_is_valid(hass: HomeAssistant, tap_dial: str) -> None:
    config = await async_validate_config_item(hass, "tap_dial", _automation(tap_dial))

    assert config is not None
    assert [trigger["subtype"] for trigger in config["triggers"]] == [
        "button_1",
        "button_2",
        "button_3",
        "button_4",
    ]


@pytest.mark.parametrize(
    ("transition", "button", "call"),
    [
        (None, "button_1", ("activate", {"entity_id": [SCENES["button_1"]], "brightness": 120})),
        (None, "button_2", ("activate", {"entity_id": [SCENES["button_2"]]})),
        (
            1.5,
            "button_3",
            ("activate", {"entity_id": [SCENES["button_3"]], "brightness": 255, "transition": 1.5}),
        ),
        (1.5, "button_2", ("activate", {"entity_id": [SCENES["button_2"]], "transition": 1.5})),
        (1.5, "button_4", ("select_next", {"entity_id": [SELECT], "cycle": True})),
    ],
)
async def test_button_press(
    hass: HomeAssistant,
    tap_dial: str,
    transition: float | None,
    button: str,
    call: tuple[str, dict[str, Any]],
) -> None:
    activate = async_mock_service(hass, "zha_dynamic_scenes", "activate")
    select_next = async_mock_service(hass, "select", "select_next")
    assert await async_setup_component(
        hass, "automation", {"automation": _automation(tap_dial, transition)}
    )

    hass.bus.async_fire(
        "zha_event", {"device_ieee": IEEE, **TRIGGERS[("remote_button_short_press", button)]}
    )
    await hass.async_block_till_done()

    calls = [(c.service, c.data) for c in (*activate, *select_next)]
    assert calls == [call]


def _automation(device_id: str, transition: float | None = None) -> dict[str, Any]:
    """Return an automation from the blueprint; button 2 keeps the palette's brightness."""
    inputs: dict[str, Any] = {
        "tap_dial": device_id,
        "zone": SELECT,
        "button_1_scene": SCENES["button_1"],
        "button_1_brightness": 120,
        "button_2_scene": SCENES["button_2"],
        "button_3_scene": SCENES["button_3"],
        "button_3_brightness": 255,
    }
    if transition is not None:
        inputs["transition"] = transition
    return {"use_blueprint": {"path": PATH, "input": inputs}}
