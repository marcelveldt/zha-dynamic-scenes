# ZHA Scenes

Play Philips Hue scenes on lamps paired to ZHA instead of a Hue bridge: the nine standard whites of the
Hue app (Relax, Read, Concentrate, ...) on every lamp, and the dynamic scenes, the gallery palettes that
animate, on Hue lamps. It ships with 44 palettes of the Hue gallery, and you can add your own.

The lamps store the scenes and run the animation themselves, just like with a Hue bridge. Home Assistant
only sends a few Zigbee frames when a scene starts: all lamps change at the same moment, no colour loop on
the host, and lamps that share a colour stay in sync.

## Requirements

- Home Assistant 2026.11 or newer, with ZHA.
- Standard scenes: any ZHA light with scenes.
- Dynamic scenes: Hue colour lamps with current firmware. Gradient lamps (Signe, gradient lightstrips) show
  the palette along the lamp. Other lights in a zone get a fixed colour or colour temperature from the
  palette.

## Installation

- **HACS:** add `https://github.com/marcelveldt/zha-scenes` as a custom repository of type
  *Integration*, install *ZHA Scenes* and restart Home Assistant.
- **Manual:** copy `custom_components/zha_scenes` into the `custom_components` folder of your
  configuration and restart Home Assistant.

## Setup

1. Add the integration under *Settings > Devices & services > Add integration > ZHA Scenes*.
2. Select *Configure* on the integration to set it up:
   - **Palettes** (optional): add your own palettes from hex colours, next to the built-in ones.
   - **Zones:** pick a ZHA group (or a single ZHA light) and choose the palettes it offers, from the
     built-in palettes and your own. A zone without palettes offers only the standard scenes.
   - **Clusters** (optional): lamps of a zone that always share a colour, for example the two lamps on
     both sides of a door.
   - **Lamp correction** (optional): some LED controllers show warm whites too cold. Enter the warmest
     white such a light really shows and the standard scenes use warmer settings on it.

## Usage

- Every zone gets a scene entity per standard scene and per palette, and a select entity to pick one.
- `scene.turn_on` starts a scene. The `zha_scenes.activate` action can override brightness and
  transition, and the speed of a palette.
- The first start of a scene stores it in the lamps. Lights of other brands switch to it without
  transition that first time.
- Lights that only switch on and off (like a relay) store no standard scenes: they are on in the scenes
  at full brightness and off in the others.
- Dimming and switching the ZHA group work as usual; the animation keeps running.

### Blueprint: Hue Tap Dial

Each of the four buttons of a Hue Tap Dial paired to ZHA starts a scene, with its own brightness. Bind
the dial to the zone's ZHA group so dimming and off work without Home Assistant.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/marcelveldt/zha-scenes/blob/main/blueprints/automation/zha_scenes/tap_dial.yaml)

## How it works

See [DEVELOPMENT.md](https://github.com/marcelveldt/zha-scenes/blob/main/DEVELOPMENT.md) for the Zigbee commands, the design and how to run the tests.
Inspired by [hass-scene_presets](https://github.com/Hypfer/hass-scene_presets).

## License

[Apache-2.0](https://github.com/marcelveldt/zha-scenes/blob/main/LICENSE)
