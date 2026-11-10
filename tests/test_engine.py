"""Start colours, lazy scene slots and LRU eviction."""

import json
from unittest.mock import Mock

import pytest

from custom_components.zha_scenes.engine import (
    FIRST_SCENE_ID,
    LAST_SCENE_ID,
    LampTarget,
    SceneCache,
    SceneEngine,
    TransportError,
    assign_targets,
)
from custom_components.zha_scenes.models import (
    Gradient,
    GradientMode,
    PaletteColor,
    ZoneConfig,
)
from custom_components.zha_scenes.protocol import (
    CMD_ADD_HUE_SCENE,
    CMD_ENHANCED_ADD_SCENE,
    CMD_REMOVE_SCENE,
    STATUS_INSUFFICIENT_SPACE,
    GradientSceneContent,
    SceneContent,
    add_scene_payload,
    dynamic_palette_payload,
    gradient_scene_payload,
    remove_scene_payload,
)

from .common import (
    BLUE,
    FEATURES_COLOR,
    FEATURES_GRADIENT,
    GREEN,
    PALETTE_GRADIENT,
    RED,
    FakeTransport,
    lamp,
    palette,
)

GROUP = 0xDD92
ZONE = ZoneConfig(group_id=GROUP, entity_id="light.zone_group")
OTHER_ZONE = ZoneConfig(group_id=0x0001, entity_id="light.other_group")
LIGHT_ZONE = ZoneConfig(group_id=None, entity_id="light.lamp_01", ieee=lamp(1).ieee)
CONTENT_A = SceneContent(level=200, x=20000, y=20000)
CONTENT_B = SceneContent(level=100, x=30000, y=10000)
GRADIENT_A = GradientSceneContent(level=200, points=((0.6, 0.3), (0.2, 0.7)), style=0x00)


def test_assign_spreads_colours_in_entity_order() -> None:
    lamps = [lamp(4), lamp(2), lamp(1), lamp(3)]
    targets = assign_targets(lamps, [], palette())
    assert [targets[lamp(n).ieee].xy for n in (1, 2, 3, 4)] == [
        RED.xy,
        GREEN.xy,
        BLUE.xy,
        RED.xy,
    ]
    assert targets[lamp(2).ieee] == LampTarget(
        xy=GREEN.xy, brightness=70.0, gradient=PALETTE_GRADIENT, mirek=400
    )


def test_assign_cluster_shares_one_colour() -> None:
    lamps = [lamp(1), lamp(2), lamp(3), lamp(4)]
    targets = assign_targets(lamps, [[lamp(4).ieee, lamp(1).ieee]], palette())
    assert targets[lamp(4).ieee] == targets[lamp(1).ieee]
    assert targets[lamp(4).ieee].xy == RED.xy
    assert targets[lamp(2).ieee].xy == GREEN.xy
    assert targets[lamp(3).ieee].xy == BLUE.xy


def test_assign_ignores_unknown_and_duplicate_cluster_members() -> None:
    lamps = [lamp(1), lamp(2)]
    clusters = [["00:00:00:00:00:00:00:99", lamp(1).ieee], [lamp(1).ieee, lamp(2).ieee]]
    targets = assign_targets(lamps, clusters, palette())
    assert targets[lamp(1).ieee].xy == RED.xy
    assert targets[lamp(2).ieee].xy == GREEN.xy


def test_assign_clusters_get_the_palette_colours_in_turn() -> None:
    lamps = [lamp(n) for n in range(1, 6)]
    clusters = [[lamp(1).ieee, lamp(2).ieee], [lamp(3).ieee, lamp(4).ieee]]
    targets = assign_targets(lamps, clusters, palette())
    assert [targets[lamp(n).ieee].xy for n in range(1, 6)] == [
        RED.xy,
        RED.xy,
        GREEN.xy,
        GREEN.xy,
        BLUE.xy,
    ]
    assert {targets[lamp(n).ieee].gradient for n in range(1, 6)} == {PALETTE_GRADIENT}


def test_assign_palette_gradient_takes_the_first_five_colours() -> None:
    colors = [PaletteColor(0.1 * n, 0.3, 50.0) for n in range(1, 8)]
    custom = palette()
    custom.colors = colors
    targets = assign_targets([lamp(1)], [], custom)
    assert targets[lamp(1).ieee].gradient == Gradient(
        points=tuple(color.xy for color in colors[:5]), mode=GradientMode.INTERPOLATED_PALETTE
    )


async def test_first_start_writes_scene_then_groupcasts() -> None:
    transport, cache, engine = _engine()
    failed = await engine.async_start(
        ZONE,
        palette(),
        [(lamp(1), CONTENT_A), (lamp(2), CONTENT_B)],
        speed=0.5,
        brightness=100,
        transition=5,
    )
    assert failed == []
    assert sorted(transport.requests) == sorted(
        [
            (lamp(1).ieee, CMD_ENHANCED_ADD_SCENE, add_scene_payload(0, 0x80, CONTENT_A, 4)),
            (lamp(2).ieee, CMD_ENHANCED_ADD_SCENE, add_scene_payload(0, 0x80, CONTENT_B, 4)),
        ]
    )
    colors = [RED.xy, GREEN.xy, BLUE.xy]
    assert transport.groupcasts == [
        (
            GROUP,
            dynamic_palette_payload(0, 0x80, colors, speed=0.5, brightness=100, transition=5),
        )
    ]
    assert cache.stored(lamp(1).ieee, 0x80) == CONTENT_A


async def test_single_light_zone_starts_with_a_unicast() -> None:
    transport, cache, engine = _engine()
    contents = [(lamp(1), CONTENT_A)]
    for _ in range(2):
        assert await engine.async_start(LIGHT_ZONE, palette(), contents, speed=0.5) == []

    payload = dynamic_palette_payload(0, 0x80, [RED.xy, GREEN.xy, BLUE.xy], speed=0.5)
    assert transport.unicasts == [(lamp(1).ieee, payload), (lamp(1).ieee, payload)]
    assert transport.groupcasts == []
    # written once, under the lamp's own zone
    assert transport.requests == [
        (lamp(1).ieee, CMD_ENHANCED_ADD_SCENE, add_scene_payload(0, 0x80, CONTENT_A, 4))
    ]
    assert cache.as_dict()["slots"] == {lamp(1).ieee: {"custom:a": {"scene_id": 0x80, "used": 2}}}


async def test_known_content_is_not_written_again() -> None:
    transport, cache, engine = _engine()
    await engine.async_start(ZONE, palette(), [(lamp(1), CONTENT_A)], speed=0.5)
    transport.requests.clear()

    await engine.async_start(
        ZONE, palette(), [(lamp(1), CONTENT_A), (lamp(2), CONTENT_A)], speed=0.5
    )
    assert [r[0] for r in transport.requests] == [lamp(2).ieee]

    transport.requests.clear()
    await engine.async_start(
        ZONE, palette(), [(lamp(1), CONTENT_B), (lamp(2), CONTENT_A)], speed=0.5
    )
    assert [r[0] for r in transport.requests] == [lamp(1).ieee]
    assert cache.stored(lamp(1).ieee, 0x80) == CONTENT_B
    assert len(transport.groupcasts) == 3


async def test_gradient_content_is_stored_with_the_signify_command() -> None:
    transport, cache, engine = _engine()
    failed = await engine.async_start(
        ZONE, palette(), [(lamp(1), GRADIENT_A), (lamp(2), CONTENT_A)], speed=0.5
    )
    assert failed == []
    assert transport.signify_requests == [
        (lamp(1).ieee, CMD_ADD_HUE_SCENE, gradient_scene_payload(0, 0x80, GRADIENT_A, 4))
    ]
    assert transport.requests == [
        (lamp(2).ieee, CMD_ENHANCED_ADD_SCENE, add_scene_payload(0, 0x80, CONTENT_A, 4))
    ]
    assert cache.stored(lamp(1).ieee, 0x80) == GRADIENT_A
    assert len(transport.groupcasts) == 1


@pytest.mark.parametrize(
    "changed",
    [
        GradientSceneContent(level=200, points=((0.6, 0.3), (0.2, 0.7)), style=0x06),
        GradientSceneContent(level=200, points=((0.6, 0.3), (0.2, 0.6)), style=0x00),
        GradientSceneContent(level=150, points=((0.6, 0.3), (0.2, 0.7)), style=0x00),
    ],
)
async def test_changed_gradient_is_written_again(changed: GradientSceneContent) -> None:
    transport, cache, engine = _engine()
    await engine.async_start(ZONE, palette(), [(lamp(1), GRADIENT_A)], speed=0.5)
    # the cache survives a restart: the same gradient is not written again
    engine = SceneEngine(transport, SceneCache(cache.as_dict(), Mock()))
    await engine.async_start(ZONE, palette(), [(lamp(1), GRADIENT_A)], speed=0.5)
    assert len(transport.signify_requests) == 1

    await engine.async_start(ZONE, palette(), [(lamp(1), changed)], speed=0.5)
    assert len(transport.signify_requests) == 2
    assert transport.signify_requests[1][2] == gradient_scene_payload(0, 0x80, changed, 4)


async def test_gradient_support_is_read_once_until_the_lamp_is_forgotten() -> None:
    transport, cache, engine = _engine()
    transport.features[lamp(1).ieee] = FEATURES_GRADIENT
    lamps = [lamp(1), lamp(2)]
    assert await engine.async_gradient_lamps(lamps) == {lamp(1).ieee}
    assert sorted(transport.feature_reads) == [lamp(1).ieee, lamp(2).ieee]

    transport.feature_reads.clear()
    restored = SceneEngine(transport, SceneCache(cache.as_dict(), Mock()))
    assert await restored.async_gradient_lamps(lamps) == {lamp(1).ieee}
    assert transport.feature_reads == []

    # rewriting a zone's scenes does not forget what the lamps are
    cache.forget_zone(ZONE.zone_id)
    cache.forget_lamp(lamp(1).ieee)
    transport.features[lamp(1).ieee] = FEATURES_COLOR
    assert await engine.async_gradient_lamps(lamps) == set()
    assert transport.feature_reads == [lamp(1).ieee]


async def test_unreadable_lamp_has_no_gradient_and_is_read_again() -> None:
    transport, cache, engine = _engine()
    transport.features[lamp(1).ieee] = None
    assert await engine.async_gradient_lamps([lamp(1)]) == set()
    assert cache.gradient(lamp(1).ieee) is None

    transport.features[lamp(1).ieee] = FEATURES_GRADIENT
    assert await engine.async_gradient_lamps([lamp(1)]) == {lamp(1).ieee}
    assert transport.feature_reads == [lamp(1).ieee, lamp(1).ieee]


async def test_each_palette_and_zone_gets_its_own_scene_id() -> None:
    transport, cache, engine = _engine()
    await engine.async_start(ZONE, palette("custom:a"), [(lamp(1), CONTENT_A)], speed=0.5)
    await engine.async_start(ZONE, palette("custom:b"), [(lamp(1), CONTENT_A)], speed=0.5)
    await engine.async_start(OTHER_ZONE, palette("custom:b"), [(lamp(1), CONTENT_A)], speed=0.5)
    assert cache.slot(ZONE.zone_id, "custom:a") == 0x80
    assert cache.slot(ZONE.zone_id, "custom:b") == 0x81
    # all scenes live in group 0x0000, so a lamp in two zones needs distinct ids
    assert cache.slot(OTHER_ZONE.zone_id, "custom:b") == 0x82
    assert len(transport.requests) == 3


async def test_forget_zone_keeps_other_zones() -> None:
    _, cache, engine = _engine()
    await engine.async_start(ZONE, palette(), [(lamp(1), CONTENT_A)], speed=0.5)
    await engine.async_start(OTHER_ZONE, palette(), [(lamp(1), CONTENT_A)], speed=0.5)
    cache.forget_zone(ZONE.zone_id)
    assert cache.stored(lamp(1).ieee, 0x80) is None
    assert cache.stored(lamp(1).ieee, 0x81) == CONTENT_A


async def test_forgotten_lamp_and_zone_are_written_again() -> None:
    transport, cache, engine = _engine()
    contents = [(lamp(1), CONTENT_A), (lamp(2), CONTENT_A)]
    await engine.async_start(ZONE, palette(), contents, speed=0.5)

    transport.requests.clear()
    cache.forget_lamp(lamp(2).ieee)
    await engine.async_start(ZONE, palette(), contents, speed=0.5)
    assert [r[0] for r in transport.requests] == [lamp(2).ieee]

    transport.requests.clear()
    cache.forget_zone(ZONE.zone_id)
    await engine.async_start(ZONE, palette(), contents, speed=0.5)
    assert len(transport.requests) == 2


async def test_insufficient_space_evicts_least_recently_used_slot() -> None:
    transport, cache, engine = _engine()
    contents = [(lamp(1), CONTENT_A), (lamp(2), CONTENT_A)]
    await engine.async_start(ZONE, palette("custom:a"), contents, speed=0.5)
    await engine.async_start(ZONE, palette("custom:b"), contents, speed=0.5)
    await engine.async_start(ZONE, palette("custom:a"), contents, speed=0.5)
    transport.requests.clear()

    transport.answers[lamp(1).ieee] = [STATUS_INSUFFICIENT_SPACE]
    failed = await engine.async_start(ZONE, palette("custom:c"), contents, speed=0.5)

    assert failed == []
    # b (0x81) is the least recently used: removed from every lamp of the zone
    removes = [r for r in transport.requests if r[1] == CMD_REMOVE_SCENE]
    assert sorted(removes) == sorted(
        (ieee, CMD_REMOVE_SCENE, remove_scene_payload(0, 0x81))
        for ieee in (lamp(1).ieee, lamp(2).ieee)
    )
    adds = [r[0] for r in transport.requests if r[1] == CMD_ENHANCED_ADD_SCENE]
    assert sorted(adds) == sorted([lamp(1).ieee, lamp(1).ieee, lamp(2).ieee])
    assert cache.slot(ZONE.zone_id, "custom:b") is None
    assert cache.slot(ZONE.zone_id, "custom:c") == 0x82
    assert cache.stored(lamp(1).ieee, 0x81) is None
    assert cache.stored(lamp(1).ieee, 0x82) == CONTENT_A


async def test_insufficient_space_without_slot_to_evict_fails_the_lamp() -> None:
    transport, cache, engine = _engine()
    transport.answers[lamp(1).ieee] = [STATUS_INSUFFICIENT_SPACE]
    failed = await engine.async_start(
        ZONE, palette(), [(lamp(1), CONTENT_A), (lamp(2), CONTENT_A)], speed=0.5
    )
    assert failed == [lamp(1)]
    assert cache.stored(lamp(1).ieee, 0x80) is None
    assert cache.stored(lamp(2).ieee, 0x80) == CONTENT_A
    assert len(transport.groupcasts) == 1


async def test_unreachable_lamp_fails_and_is_retried_next_time() -> None:
    transport, cache, engine = _engine()
    transport.answers[lamp(1).ieee] = [None]
    failed = await engine.async_start(ZONE, palette(), [(lamp(1), CONTENT_A)], speed=0.5)
    assert failed == [lamp(1)]
    assert cache.stored(lamp(1).ieee, 0x80) is None

    transport.requests.clear()
    assert await engine.async_start(ZONE, palette(), [(lamp(1), CONTENT_A)], speed=0.5) == []
    assert len(transport.requests) == 1


async def test_groupcast_failure_propagates() -> None:
    transport, _, engine = _engine()
    transport.async_start_dynamic = Mock(side_effect=TransportError("down"))  # type: ignore[method-assign]
    with pytest.raises(TransportError):
        await engine.async_start(ZONE, palette(), [(lamp(1), CONTENT_A)], speed=0.5)


def test_allocate_reuses_least_recently_used_id_when_full() -> None:
    slots = {
        f"custom:{scene_id}": {"scene_id": scene_id, "used": 1000 - scene_id}
        for scene_id in range(FIRST_SCENE_ID, LAST_SCENE_ID + 1)
    }
    data = {
        "slots": {str(GROUP): slots},
        "scenes": {lamp(1).ieee: {str(LAST_SCENE_ID): [1, 2, 3]}},
    }
    cache = SceneCache(data, Mock())
    assert cache.allocate(ZONE.zone_id, "custom:new") == LAST_SCENE_ID
    assert cache.slot(ZONE.zone_id, f"custom:{LAST_SCENE_ID}") is None
    assert cache.stored(lamp(1).ieee, LAST_SCENE_ID) is None


def test_allocate_reuses_least_recently_used_id_of_any_zone() -> None:
    slots = {
        f"custom:{scene_id}": {"scene_id": scene_id, "used": scene_id}
        for scene_id in range(FIRST_SCENE_ID, LAST_SCENE_ID)
    }
    other = {"custom:old": {"scene_id": LAST_SCENE_ID, "used": 0}}
    cache = SceneCache({"slots": {str(GROUP): slots, "1": other}}, Mock())
    assert cache.allocate(ZONE.zone_id, "custom:new") == LAST_SCENE_ID
    assert cache.slot("1", "custom:old") is None


def test_cache_state_round_trips_and_saves() -> None:
    save = Mock()
    cache = SceneCache({}, save)
    cache.allocate(ZONE.zone_id, "custom:a")
    cache.set_stored(lamp(1).ieee, 0x80, CONTENT_A)
    cache.set_stored(lamp(2).ieee, 0x80, GRADIENT_A)
    cache.set_gradient(lamp(2).ieee, gradient=True)
    assert save.call_count == 4
    restored = SceneCache(json.loads(json.dumps(cache.as_dict())), Mock())
    assert restored.slot(ZONE.zone_id, "custom:a") == 0x80
    assert restored.stored(lamp(1).ieee, 0x80) == CONTENT_A
    assert restored.stored(lamp(2).ieee, 0x80) == GRADIENT_A
    assert restored.gradient(lamp(2).ieee) is True
    assert restored.gradient(lamp(1).ieee) is None


def _engine() -> tuple[FakeTransport, SceneCache, SceneEngine]:
    transport = FakeTransport()
    cache = SceneCache({}, Mock())
    return transport, cache, SceneEngine(transport, cache)
