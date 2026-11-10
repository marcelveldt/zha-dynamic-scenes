# Development

How the integration works, for contributors. Usage is in the [README](README.md).

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

- **Standard scene**: one of the nine whites of the Hue app (Dimmed, Relax, Read, Bright, Nightlight,
  Concentrate, Energize, Rest, Cool bright) with a fixed brightness and colour temperature. Every zone offers
  all of them.
- **Palette**: colours (CIE xy with a brightness), a speed and an optional colour temperature for lamps
  without colour. A palette is built-in (see below) or custom, added in the options.
- **Zone**: a ZHA group. A start is one groupcast to the group, so all lamps start at the same moment. A
  zone can also be a single ZHA light (ZHA makes no group light for a group of one): its starts are unicasts
  to the lamp, and it has no clusters. A zone offers the standard scenes and the palettes chosen for it, at
  least one.
- **Cluster**: lamps of a zone that must share a start colour, for example a pair of lamps on both sides
  of a door. Every other lamp is a cluster of its own.

## Layout

```
custom_components/zha_scenes/
  protocol.py     ZCL frame encoding, no Home Assistant or zigpy imports
  engine.py       start colours, scene cache, palette start and standard scene recall over a transport,
                  lamp correction
  zigbee.py       ZHA access: zone lamps, lamp capabilities, frames through ZHA's zigpy application
  manager.py      runtime: palette library, zones, activation (Zigbee and plain light calls)
  builtin.py      the built-in palettes (palettes.json), the standard scenes and their translated names
  models.py       palettes, standard scenes, zones, lamps
  config_flow.py  setup and the options flow for palettes, zones, clusters and lamp corrections
  scene.py        one scene entity per standard scene and per palette a zone offers
  select.py       one select entity per zone
  services.py     `activate` on the scene entities, `rewrite_scenes` on the select entities
```

Dependencies point inwards: `protocol` knows nothing else, `engine` only knows `protocol` and the models,
`zigbee` is the only module that touches ZHA and zigpy, and `manager` wires them together.

## Entities

Every zone has, on the device of its ZHA group (or of its single light), a select entity with the
standard scenes and the palettes it offers, and a scene entity per standard scene and per offered palette.
Standard scenes and built-in palettes take their names from the translations, a custom palette its own
name; the keys of the standard scenes never match a palette id, since both make up the unique ids.
`scene.turn_on` starts the scene with its own brightness (and a palette with its own speed); the `activate`
service can override brightness and transition, and a palette's speed. The scene entities follow the
library and the zone setup: scene entities of palettes a zone no longer offers are removed, also from the
entity registry.

## Built-in palettes

`palettes.json` holds the 44 palettes of the Hue gallery: per palette an id, the English name, the colours
(CIE xy with a brightness in percent), the speed and optionally a colour temperature for lamps without
colour. The names shown in Home Assistant come from the translations (`entity.scene.<palette id>.name`), so
every palette needs a name in each translation file; a test checks that. The file is read once and never
stored: the Home Assistant storage holds only the custom palettes and the scene cache, and the palette ids
in a zone refer to either kind.

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

## Standard scenes

A standard scene is a plain ZCL scene with a fixed id (`10`-`18`, in the order of the concepts above),
stored in the lamps under the zone's own group; a single-light zone stores it under group `0x0000`. The
zone's group gives every zone the same fixed ids without clashes between zones that share a lamp, and lamps
of any brand recall it. Activating one is a Recall Scene groupcast to the zone's group (a unicast for a
single light), with the transition when one is given. A brightness follows as a Move to Level (without
on/off) to the same group, in the same transition.

Like palettes, standard scenes are written when they are activated, and only to lamps whose stored content
is unknown or different:

- **Signify lamps** take it silently with an Enhanced Add Scene: on, level and, for lamps with colour
  temperature, the colour set the way Hue lamps report their stored scenes: x, y, enhanced hue, saturation,
  colour loop active, direction and time, colour temperature (13 bytes). x and y stay 0: with xy filled in
  a colour lamp recalls the scene in xy mode.
- **Other lamps** do not all take that set; a GLEDOPTO LED controller stores only a colour temperature and
  recalls the 13-byte set as its coldest white. They get an Enhanced Add Scene without state, are set to the
  scene through Home Assistant (`light.turn_on` without transition, which is their activation) and store
  their state with Store Scene a second later.

A lamp without room for the scene (a GLEDOPTO holds 8 scenes) is skipped with a warning; nothing is evicted
for a standard scene. A lamp that could not store the scene is set through Home Assistant instead, with the
zone's brightness and transition.

### Lamp correction

Some LED controllers assume warmer LEDs than the strip has, so warm whites look too cold. The options take
per lamp the warmest white it really shows, in Kelvin (`corrections` in the entry options, by IEEE). It
only changes the colour temperature of the standard scenes on that lamp. With W that white and M the lamp's
warmest setting (ZHA's `max_mireds`), both in mired, and the pivot P = 300 mired, a colour temperature m
becomes

- m <= P: m
- m > P: min(M, P + (m - P) * (M - P) / (W - P))

so W maps to M and colder whites stay as they are. W lies between P and M. A changed correction changes
what the lamp should store, so its standard scenes are written again on their next activation.

## Scene slots and the cache

Scenes are written lazily: a palette gets a scene id (`0x80`-`0xFE`) the first time it is started in a
zone, and a lamp is written only when what it should store differs from what it is known to store. After
the first start, starting a palette or standard scene costs one groupcast (a unicast for a single light)
and no flash writes in the lamps.

Like the Hue bridge, palette scenes are stored in group `0x0000`; the zone's group only addresses the
groupcast, so a single light needs no group. Scenes of a real group would be deleted when a lamp leaves
that group or receives a Remove All Scenes for it. Since a lamp can be in several zones, palette scene ids
are unique over all zones; when all are taken, the least recently used one is reused.

The cache of slots, stored contents and which lamps show gradients is kept in Home Assistant storage
(`.storage/zha_scenes`), next to the custom palettes. Stored contents are keyed by lamp IEEE and by
`<group id>:<scene id>`, both decimal. A standard scene's content is `{"level": <1..254>, "mireds": <mired
or null>}`, so lamps that already hold a standard scene can be seeded, for example
`"scenes": {"00:17:88:01:00:00:00:01": {"56722:11": {"level": 143, "mireds": 447}}}`.

The cache is cleared for a lamp when ZHA reports it joined with a new address (a reset lamp does) or was
removed, and for a group's standard scenes when a lamp leaves that group. The `rewrite_scenes` service
clears a zone's scenes by hand, for scenes removed by other means. Each is written again on its next
activation.

When a lamp's scene table is full, the zone's least recently used palette slot is removed from its lamps
and the write is retried once.

## Design choices

- **Transport through ZHA's zigpy application.** The dynamic command reuses the id of Add Scene and the
  gradient scene command that of Remove Scene, so ZHA's cluster command service would encode them as those
  standard commands. Frames are therefore built here and sent raw: the unicasts through the zigpy endpoint
  (so ZHA's request handling, retries and response matching apply) and the groupcast through the zigpy
  group, like ZHA's own group commands.
- **No polling or state tracking.** Lamps do not report a running palette, so the select entity shows the
  scene started last, by any entity or service.
- **No Hue bridge needed.** The built-in palettes ship with the integration; nothing is read from or
  written to a bridge.

## Tests and checks

```bash
uv venv && uv pip install -r requirements_test.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest -q
```
