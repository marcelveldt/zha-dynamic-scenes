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

## Concepts

- **Palette**: colours (CIE xy with a brightness), a speed, an optional colour temperature for lamps without
  colour, and for imported Hue scenes the start state Hue gave each lamp, keyed by IEEE address. A Hue
  lamp keeps its IEEE address when it moves to ZHA, so the per-lamp start colours survive the move.
- **Zone**: a ZHA group. The palette start is one groupcast to the group, so all lamps start at the same
  moment.
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
  select.py       one select entity per zone
  services.py     `activate` and `rewrite_scenes`, targeting the zone select entities
```

Dependencies point inwards: `protocol` knows nothing else, `engine` only knows `protocol` and the models,
`zigbee` is the only module that touches ZHA and zigpy, and `manager` wires them together.

## Activating a palette

1. `zigbee` resolves the zone's ZHA group to its lights and classifies each one: a Signify colour lamp with
   the Hue effects cluster (`0xFC03`) on its light endpoint animates (`dynamic`); other lights are colour,
   colour temperature or plain lights.
2. `engine.assign_targets` gives every cluster a start state: the Hue scene action of one of its lamps if
   the palette has one, otherwise the next palette colour. The result only depends on the zone and the
   palette, so a lamp starts with the same colour every time.
3. For the dynamic lamps, `SceneEngine` makes sure the palette's scene is stored: it gives the palette a
   scene id in the zone and sends a unicast Add Scene (on, level, xy) only to lamps whose stored content is
   unknown or different. Then it sends the dynamic palette command once to the group.
4. The other lights get a plain `light.turn_on` with their start colour or the palette's colour
   temperature. A dynamic lamp that could not store the scene gets the same treatment.

## Scene slots and the cache

Scenes are written lazily: a palette gets a scene id (`0x80`-`0xFE`, per zone) the first time it is started
in that zone, and a lamp is written only when what it should store differs from what it is known to store.
After the first start, starting a palette costs one groupcast and no flash writes in the lamps.

The cache of slots and stored contents is kept in Home Assistant storage, next to the palette library. It
is cleared for a lamp when ZHA reports it joined with a new address (a reset lamp does) or was removed, and
for a zone when its group loses a member (leaving a group deletes the member's scenes of that group). The
`rewrite_scenes` service clears a zone by hand, for scenes removed by other means.

When a lamp's scene table is full, the zone's least recently used slot is removed from its lamps and the
write is retried once.

## Design choices

- **Transport through ZHA's zigpy application.** The dynamic command reuses the id of Add Scene, so ZHA's
  cluster command service would encode it as Add Scene. Frames are therefore built here and sent raw: the
  unicasts through the zigpy endpoint (so ZHA's request handling, retries and response matching apply) and
  the groupcast through the zigpy group, like ZHA's own group commands.
- **No polling or state tracking.** Lamps do not report a running palette, so the select entity shows the
  last started palette.
- **The library is independent of the bridge.** Imported palettes stay usable after the lamps and the bridge
  part ways; nothing is written to the bridge.
