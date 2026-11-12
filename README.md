# ZHA Scenes

Hue scenes for lamps on ZHA, without a Hue bridge.

<!-- ha-marketplace:exclude-start -->
[![Open your Home Assistant instance and show ZHA Scenes in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=marcelveldt&repository=zha-scenes&category=integration)
<!-- ha-marketplace:exclude-end -->

Moved your Philips Hue lamps from the Hue bridge to ZHA? ZHA Scenes brings back the scenes you know
from the Hue app:

- **Standard scenes:** the nine whites of the Hue app, like Relax, Read, Concentrate and Nightlight, on
  any ZHA light.
- **Dynamic scenes:** 44 animated scenes from the Hue gallery, like Golden pond and Rolling hills.
  Gradient lamps, like the Signe, show the colours along the lamp.
- **Your own scenes:** set the lights the way you like and save that as a scene, or make a dynamic scene
  from your own colours.

## Why scenes in the lamps

ZHA Scenes stores every scene in the lamps themselves, just like a Hue bridge does:

- **One command starts a scene.** Home Assistant sends a single Zigbee command to the whole zone, and
  every lamp switches at the same moment. Only the very first start stores the scene in each lamp.
- **The lamps play the animation.** Once started, a dynamic scene runs on the lamps on its own, without
  any further Zigbee traffic. Integrations that animate colours from Home Assistant send every lamp a
  new colour every few seconds: with ten lamps that is easily over ten thousand commands an hour, which
  can slow down the rest of your Zigbee network.
- **Easy to use.** Pick a scene instead of setting the brightness and colour of every lamp. Each scene is
  a regular scene entity for dashboards, automations and voice assistants.

## Requirements

- Home Assistant 2026.11 or newer, with ZHA.
- Standard and saved scenes work on every ZHA light that supports scenes. A light that only switches
  on and off is on in the standard scenes at full brightness and off in the others, and a saved scene
  keeps it the way you saved it.
- Dynamic scenes need Hue colour lamps with up-to-date firmware. Other lights in the same zone get a
  fixed colour or white from the scene.

## Installation

1. Select the button above, or open **Settings > Marketplace** and add
   `https://github.com/marcelveldt/zha-scenes` as a custom repository of type *Integration*.
2. Download **ZHA Scenes** and restart Home Assistant.

Without the Marketplace, copy `custom_components/zha_scenes` into the `custom_components` folder of your
configuration and restart Home Assistant.

## Getting started

1. Go to **Settings > Devices & services > Add integration** and pick **ZHA Scenes**.
2. Select **Configure** on ZHA Scenes, then **Add a zone**. A zone is a ZHA group whose lights change
   scene together, or a single ZHA light.
3. Choose the scenes the zone offers and select **Save**. A new zone has all standard scenes; add the
   dynamic scenes you like.
4. Start a scene with the zone's **Scene** select, or with its scene entities from a dashboard, an
   automation or a voice assistant.

The first start of a scene stores it in the lamps, which takes a moment. After that it starts right
away.

## Managing a zone

Select **Configure > Edit a zone**, make your changes and select **Save**:

- **Choose the scenes:** untick the standard scenes a zone never uses, and add or remove dynamic
  scenes. Standard scenes you untick are also removed from the lamps when you save the zone.
- **Save the current state as a scene:** set the lights the way you like first. Every lamp keeps its
  on or off state, brightness and colour. Rename, update or delete it later with **Edit a saved scene**.
- **Clusters:** lamps that always share the same colour in a dynamic scene, for example the two lamps on
  either side of a door.

In the main **Configure** menu:

- **Custom dynamic scenes:** make a dynamic scene from your own colours (hex codes).
- **Lamp correction:** some LED controllers show warm white colder than asked. Enter the warmest white
  such a light really shows, and the standard scenes look right on it.

## Automations

Every scene is a scene entity, so `scene.turn_on` works everywhere. The **Activate** action can also
set the brightness, the transition and the speed of a dynamic scene:

```yaml
action: zha_scenes.activate
target:
  entity_id: scene.living_room_golden_pond
data:
  brightness: 150 # 1 to 255, for all lights of the zone
  transition: 2 # seconds
  speed: 0.4 # 0 to 1, dynamic scenes only
```

Dimming and switching the ZHA group work as usual, and a dynamic scene keeps playing.

### Hue Tap Dial blueprint

Gives each of the four buttons of a Hue Tap Dial a scene with its own brightness. Bind the dial to the
zone's ZHA group, so dimming and switching off also work without Home Assistant.

[![Open your Home Assistant instance and show the blueprint import dialog with the Tap Dial blueprint.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/marcelveldt/zha-scenes/blob/main/blueprints/automation/zha_scenes/tap_dial.yaml)

## Good to know

- Lights of other brands switch to a scene without a transition the first time it starts.
- Were the scenes wiped from the lamps, for example by a factory reset? Run the **Rewrite scenes**
  action on the zone's Scene select, and each scene is stored again on its next start.

## How it works

[DEVELOPMENT.md](https://github.com/marcelveldt/zha-scenes/blob/main/DEVELOPMENT.md) explains the Zigbee
commands, the design and how to run the tests. Inspired by
[hass-scene_presets](https://github.com/Hypfer/hass-scene_presets).

## License

[Apache-2.0](https://github.com/marcelveldt/zha-scenes/blob/main/LICENSE). ZHA Scenes is an independent
project, not affiliated with Signify or Philips Hue.
