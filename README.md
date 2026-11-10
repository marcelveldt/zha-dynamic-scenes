# ZHA dynamic scenes

Play Philips Hue dynamic scenes, the gallery palettes that animate, on Hue lamps paired to ZHA instead of
a Hue bridge.

The lamps run the animation themselves, just like with a Hue bridge. Home Assistant only sends a few Zigbee
frames when a scene starts: no colour loop on the host, and lamps that share a colour stay in sync.

## Requirements

- Home Assistant 2026.11 or newer, with ZHA.
- Hue colour lamps with current firmware. Gradient lamps (Signe, gradient lightstrips) show the palette
  along the lamp. Other lights in a zone get a fixed colour or colour temperature from the palette.

## Installation

- **HACS:** add `https://github.com/marcelveldt/zha-dynamic-scenes` as a custom repository of type
  *Integration*, install *ZHA dynamic scenes* and restart Home Assistant.
- **Manual:** copy `custom_components/zha_dynamic_scenes` into the `custom_components` folder of your
  configuration and restart Home Assistant.

## Setup

1. Add the integration under *Settings > Devices & services > Add integration > ZHA dynamic scenes*.
2. Open its options to set it up:
   - **Palettes:** import the dynamic scenes of a Hue bridge (read-only), or add your own from hex colours.
   - **Zones:** pick a ZHA group (or a single ZHA light) and the palettes it offers.
   - **Clusters** (optional): lamps of a zone that always share a colour, for example the two lamps on
     both sides of a door.

## Usage

- Every zone gets a scene entity per palette and a select entity to pick one.
- `scene.turn_on` starts a palette. The `zha_dynamic_scenes.activate` action can override brightness,
  speed and transition.
- Dimming and switching the ZHA group work as usual; the animation keeps running.

### Blueprint: Hue Tap Dial

Each of the four buttons of a Hue Tap Dial paired to ZHA starts a palette, with its own brightness. Bind
the dial to the zone's ZHA group so dimming and off work without Home Assistant.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/marcelveldt/zha-dynamic-scenes/blob/main/blueprints/automation/zha_dynamic_scenes/tap_dial.yaml)

## How it works

See [DEVELOPMENT.md](DEVELOPMENT.md) for the Zigbee commands, the design and how to run the tests.
Inspired by [hass-scene_presets](https://github.com/Hypfer/hass-scene_presets).

## License

[Apache-2.0](LICENSE)
