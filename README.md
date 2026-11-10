# ZHA dynamic scenes

A Home Assistant custom integration that plays Philips Hue dynamic scenes (the gallery palettes the lamps
animate themselves) on Hue lamps that are paired to ZHA instead of a Hue bridge.

It uses the same Zigbee mechanism as the Hue bridge: the lamps run the animation. Home Assistant only sends
a few frames when a palette starts; there is no colour loop on the host.

## How Hue starts a dynamic scene

Hue lamps (colour lamps with current firmware) start a palette on a Signify manufacturer specific command
of the Scenes cluster (manufacturer `0x100B`, command `0x00`). The command names a scene that is stored in
the lamp, plus the palette colours, the speed and optionally a brightness and transition. Each lamp first
applies the stored scene (on, level, colour) and then animates the palette, starting at the palette colour
nearest to the stored colour.

That gives two properties this integration builds on:

- The referenced scene must exist in the lamp. Lamps without it ignore the command.
- The stored colour decides where a lamp starts. Lamps that store the same colour and start together stay
  in sync; different colours spread the palette over the room, like Hue does.

Gradient lamps (for example the Signe gradient) start from the same command, but their stored scene holds a
gradient: up to five colours along the lamp with a style (Hue's `gradient.mode`). The Hue bridge stores it
with another Signify Scenes command (`0x02`) that carries on, level, transition and the gradient.

## Concepts

- **Palette**: colours (CIE xy with a brightness), a speed, an optional colour temperature for lamps without
  colour, and for imported Hue scenes the IEEE addresses of the scene's lamps, which decide the zones that
  offer it. A Hue lamp keeps its IEEE address when it moves to ZHA.
- **Zone**: a ZHA group. The palette start is one groupcast to the group, so all lamps start at the same
  moment. A zone can also be a single ZHA light (ZHA makes no group light for a group of one): its palette
  start is a unicast to the lamp, and it has no clusters.
- **Cluster**: lamps of a zone that must share a start colour, for example a pair of lamps on both sides
  of a door. Every other lamp is a cluster of its own.

## Layout

```
custom_components/zha_dynamic_scenes/
  protocol.py     ZCL frame encoding, no Home Assistant or zigpy imports
  engine.py       start colour assignment, scene slot cache, palette start over a transport
  zigbee.py       ZHA access: zone lamps, lamp capabilities, frames through ZHA's zigpy application
  manager.py      runtime: palette library, zones, activation (Zigbee and plain light calls)
  hue_import.py   read-only import of palettes from a Hue bridge (CLIP v2)
  models.py       palettes, zones, lamps
  config_flow.py  setup and the options flow for palettes, zones and clusters
  scene.py        one scene entity per palette a zone offers
  select.py       one select entity per zone
  services.py     `activate` on the scene entities, `rewrite_scenes` on the select entities
```

Dependencies point inwards: `protocol` knows nothing else, `engine` only knows `protocol` and the models,
`zigbee` is the only module that touches ZHA and zigpy, and `manager` wires them together.

## Entities

Every zone has, on the device of its ZHA group (or of its single light), a select entity with the
palettes it offers and a scene entity per offered palette. `scene.turn_on` starts the palette with its own
speed and brightness; the `activate` service can override speed, brightness and transition. The scene
entities follow the library and the zone setup: scene entities of palettes a zone no longer offers are
removed, also from the entity registry.

## Blueprints

`blueprints/automation/zha_dynamic_scenes/` holds automation blueprints built on these entities. Import one
in Home Assistant under *Settings > Automations & scenes > Blueprints > Import blueprint* with its GitHub
URL.

- `tap_dial.yaml`: a Hue Tap Dial (RDM002) paired to ZHA. Buttons 1 to 3 each start a palette through
  `activate`, with their own brightness; button 4 steps through the zone's palettes with
  `select.select_next`. Dimming and off stay out of the automation: bind the dial to the zone's ZHA group
  so they work without Home Assistant.

## Activating a palette

1. `zigbee` resolves the zone's ZHA group (or its single light) to its available lights and classifies
   each one: a Signify colour lamp with the Hue effects cluster (`0xFC03`) on its light endpoint animates
   (`dynamic`); other lights are colour, colour temperature or plain lights.
2. `engine.assign_targets` gives every cluster the next palette colour as start colour, and gradient lamps
   the first five palette colours as gradient. The result only depends on the zone and the palette, so a
   lamp starts with the same colour every time.
3. For the dynamic lamps, `SceneEngine` makes sure the palette's scene is stored: it gives the palette a
   scene id and writes the scene only to lamps whose stored content is unknown or different, with a unicast
   Enhanced Add Scene (on, level, xy) or, for gradient lamps, the Signify command (on, level, gradient).
   Whether a lamp shows gradients is read once from the Hue effects cluster (manufacturer specific
   attribute `0x0001`, bit `0x08`). Then it sends the dynamic palette command once to the zone's group,
   or as a unicast to the lamp of a single-light zone.
4. The other lights get a plain `light.turn_on` with their start colour or the palette's colour
   temperature. A dynamic lamp that could not store the scene gets the same treatment, a gradient lamp with
   the first colour of its gradient.

Brightness percentages become levels the way the Hue bridge maps them,
`1 + floor(percent * 253 / 100 + 0.5)`, so a percentage gives the same level as under the bridge.

## Scene slots and the cache

Scenes are written lazily: a palette gets a scene id (`0x80`-`0xFE`) the first time it is started in a
zone, and a lamp is written only when what it should store differs from what it is known to store. After
the first start, starting a palette costs one groupcast (a unicast for a single light) and no flash writes
in the lamps.

Like the Hue bridge, the scenes are stored in group `0x0000`; the zone's group only addresses the
groupcast, so a single light needs no group. Scenes of a real group would be deleted when a lamp leaves
that group or receives a Remove All Scenes for it. Since a lamp can be in several zones, scene ids are
unique over all zones; when all are taken, the least recently used one is reused.

The cache of slots, stored contents and which lamps show gradients is kept in Home Assistant storage, next
to the palette library. It is cleared for a lamp when ZHA reports it joined with a new address (a reset lamp
does) or was removed. The `rewrite_scenes` service clears a zone's scenes by hand, for scenes removed by
other means.

When a lamp's scene table is full, the zone's least recently used slot is removed from its lamps and the
write is retried once.

## Design choices

- **Transport through ZHA's zigpy application.** The dynamic command reuses the id of Add Scene and the
  gradient scene command that of Remove Scene, so ZHA's cluster command service would encode them as those
  standard commands. Frames are therefore built here and sent raw: the unicasts through the zigpy endpoint
  (so ZHA's request handling, retries and response matching apply) and the groupcast through the zigpy
  group, like ZHA's own group commands.
- **No polling or state tracking.** Lamps do not report a running palette, so the select entity shows the
  palette started last, by any entity or service.
- **The library is independent of the bridge.** Imported palettes stay usable after the lamps and the bridge
  part ways; nothing is written to the bridge.
