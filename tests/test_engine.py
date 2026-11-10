"""Start colours, lazy scene slots and LRU eviction."""

from unittest.mock import Mock

import pytest

from custom_components.zha_dynamic_scenes.engine import (
    FIRST_SCENE_ID,
    LAST_SCENE_ID,
    LampTarget,
    SceneCache,
    SceneEngine,
    TransportError,
    assign_targets,
)
from custom_components.zha_dynamic_scenes.models import LampKind, LightAction
from custom_components.zha_dynamic_scenes.protocol import (
    CMD_ENHANCED_ADD_SCENE,
    CMD_REMOVE_SCENE,
    STATUS_INSUFFICIENT_SPACE,
    SceneContent,
    add_scene_payload,
    dynamic_palette_payload,
    remove_scene_payload,
)

from .common import BLUE, GREEN, RED, FakeTransport, lamp, palette

GROUP = 0xDD92
CONTENT_A = SceneContent(level=200, x=20000, y=20000)
CONTENT_B = SceneContent(level=100, x=30000, y=10000)


def test_assign_spreads_colours_in_entity_order() -> None:
    lamps = [lamp(4), lamp(2), lamp(1), lamp(3)]
    targets = assign_targets(lamps, [], palette())
    assert [targets[lamp(n).ieee].xy for n in (1, 2, 3, 4)] == [
        RED.xy,
        GREEN.xy,
        BLUE.xy,
        RED.xy,
    ]
    assert targets[lamp(2).ieee] == LampTarget(xy=GREEN.xy, brightness=70.0, mirek=400)


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


def test_assign_uses_hue_actions() -> None:
    lamps = [lamp(1), lamp(2), lamp(3, LampKind.COLOR_TEMP), lamp(4)]
    actions = {
        lamp(2).ieee: LightAction(xy=(0.4, 0.4), brightness=33.0),
        lamp(3).ieee: LightAction(mirek=250, brightness=44.0),
    }
    targets = assign_targets(lamps, [], palette("hue:x", actions=actions))
    assert targets[lamp(2).ieee] == LampTarget(xy=(0.4, 0.4), brightness=33.0, mirek=400)
    # a colour temperature action keeps its own brightness and mirek; the colour is spread
    assert targets[lamp(3).ieee] == LampTarget(xy=GREEN.xy, brightness=44.0, mirek=250)
    assert targets[lamp(1).ieee].xy == RED.xy
    assert targets[lamp(4).ieee].xy == BLUE.xy


def test_assign_cluster_takes_action_of_a_member() -> None:
    lamps = [lamp(1), lamp(2), lamp(3)]
    actions = {lamp(3).ieee: LightAction(xy=(0.5, 0.4), brightness=10.0)}
    targets = assign_targets(
        lamps, [[lamp(1).ieee, lamp(3).ieee]], palette("hue:x", actions=actions)
    )
    assert targets[lamp(1).ieee] == targets[lamp(3).ieee]
    assert targets[lamp(1).ieee].xy == (0.5, 0.4)
    assert targets[lamp(2).ieee].xy == RED.xy


async def test_first_start_writes_scene_then_groupcasts() -> None:
    transport, cache, engine = _engine()
    failed = await engine.async_start(
        GROUP,
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


async def test_known_content_is_not_written_again() -> None:
    transport, cache, engine = _engine()
    await engine.async_start(GROUP, palette(), [(lamp(1), CONTENT_A)], speed=0.5)
    transport.requests.clear()

    await engine.async_start(
        GROUP, palette(), [(lamp(1), CONTENT_A), (lamp(2), CONTENT_A)], speed=0.5
    )
    assert [r[0] for r in transport.requests] == [lamp(2).ieee]

    transport.requests.clear()
    await engine.async_start(
        GROUP, palette(), [(lamp(1), CONTENT_B), (lamp(2), CONTENT_A)], speed=0.5
    )
    assert [r[0] for r in transport.requests] == [lamp(1).ieee]
    assert cache.stored(lamp(1).ieee, 0x80) == CONTENT_B
    assert len(transport.groupcasts) == 3


async def test_each_palette_and_zone_gets_its_own_scene_id() -> None:
    transport, cache, engine = _engine()
    await engine.async_start(GROUP, palette("custom:a"), [(lamp(1), CONTENT_A)], speed=0.5)
    await engine.async_start(GROUP, palette("custom:b"), [(lamp(1), CONTENT_A)], speed=0.5)
    await engine.async_start(0x0001, palette("custom:b"), [(lamp(1), CONTENT_A)], speed=0.5)
    assert cache.slot(GROUP, "custom:a") == 0x80
    assert cache.slot(GROUP, "custom:b") == 0x81
    # all scenes live in group 0x0000, so a lamp in two zones needs distinct ids
    assert cache.slot(0x0001, "custom:b") == 0x82
    assert len(transport.requests) == 3


async def test_forget_zone_keeps_other_zones() -> None:
    _, cache, engine = _engine()
    await engine.async_start(GROUP, palette(), [(lamp(1), CONTENT_A)], speed=0.5)
    await engine.async_start(0x0001, palette(), [(lamp(1), CONTENT_A)], speed=0.5)
    cache.forget_zone(GROUP)
    assert cache.stored(lamp(1).ieee, 0x80) is None
    assert cache.stored(lamp(1).ieee, 0x81) == CONTENT_A


async def test_forgotten_lamp_and_zone_are_written_again() -> None:
    transport, cache, engine = _engine()
    contents = [(lamp(1), CONTENT_A), (lamp(2), CONTENT_A)]
    await engine.async_start(GROUP, palette(), contents, speed=0.5)

    transport.requests.clear()
    cache.forget_lamp(lamp(2).ieee)
    await engine.async_start(GROUP, palette(), contents, speed=0.5)
    assert [r[0] for r in transport.requests] == [lamp(2).ieee]

    transport.requests.clear()
    cache.forget_zone(GROUP)
    await engine.async_start(GROUP, palette(), contents, speed=0.5)
    assert len(transport.requests) == 2


async def test_insufficient_space_evicts_least_recently_used_slot() -> None:
    transport, cache, engine = _engine()
    contents = [(lamp(1), CONTENT_A), (lamp(2), CONTENT_A)]
    await engine.async_start(GROUP, palette("custom:a"), contents, speed=0.5)
    await engine.async_start(GROUP, palette("custom:b"), contents, speed=0.5)
    await engine.async_start(GROUP, palette("custom:a"), contents, speed=0.5)
    transport.requests.clear()

    transport.answers[lamp(1).ieee] = [STATUS_INSUFFICIENT_SPACE]
    failed = await engine.async_start(GROUP, palette("custom:c"), contents, speed=0.5)

    assert failed == []
    # b (0x81) is the least recently used: removed from every lamp of the zone
    removes = [r for r in transport.requests if r[1] == CMD_REMOVE_SCENE]
    assert sorted(removes) == sorted(
        (ieee, CMD_REMOVE_SCENE, remove_scene_payload(0, 0x81))
        for ieee in (lamp(1).ieee, lamp(2).ieee)
    )
    adds = [r[0] for r in transport.requests if r[1] == CMD_ENHANCED_ADD_SCENE]
    assert sorted(adds) == sorted([lamp(1).ieee, lamp(1).ieee, lamp(2).ieee])
    assert cache.slot(GROUP, "custom:b") is None
    assert cache.slot(GROUP, "custom:c") == 0x82
    assert cache.stored(lamp(1).ieee, 0x81) is None
    assert cache.stored(lamp(1).ieee, 0x82) == CONTENT_A


async def test_insufficient_space_without_slot_to_evict_fails_the_lamp() -> None:
    transport, cache, engine = _engine()
    transport.answers[lamp(1).ieee] = [STATUS_INSUFFICIENT_SPACE]
    failed = await engine.async_start(
        GROUP, palette(), [(lamp(1), CONTENT_A), (lamp(2), CONTENT_A)], speed=0.5
    )
    assert failed == [lamp(1)]
    assert cache.stored(lamp(1).ieee, 0x80) is None
    assert cache.stored(lamp(2).ieee, 0x80) == CONTENT_A
    assert len(transport.groupcasts) == 1


async def test_unreachable_lamp_fails_and_is_retried_next_time() -> None:
    transport, cache, engine = _engine()
    transport.answers[lamp(1).ieee] = [None]
    failed = await engine.async_start(GROUP, palette(), [(lamp(1), CONTENT_A)], speed=0.5)
    assert failed == [lamp(1)]
    assert cache.stored(lamp(1).ieee, 0x80) is None

    transport.requests.clear()
    assert await engine.async_start(GROUP, palette(), [(lamp(1), CONTENT_A)], speed=0.5) == []
    assert len(transport.requests) == 1


async def test_groupcast_failure_propagates() -> None:
    transport, _, engine = _engine()
    transport.async_start_dynamic = Mock(side_effect=TransportError("down"))  # type: ignore[method-assign]
    with pytest.raises(TransportError):
        await engine.async_start(GROUP, palette(), [(lamp(1), CONTENT_A)], speed=0.5)


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
    assert cache.allocate(GROUP, "custom:new") == LAST_SCENE_ID
    assert cache.slot(GROUP, f"custom:{LAST_SCENE_ID}") is None
    assert cache.stored(lamp(1).ieee, LAST_SCENE_ID) is None


def test_allocate_reuses_least_recently_used_id_of_any_zone() -> None:
    slots = {
        f"custom:{scene_id}": {"scene_id": scene_id, "used": scene_id}
        for scene_id in range(FIRST_SCENE_ID, LAST_SCENE_ID)
    }
    other = {"custom:old": {"scene_id": LAST_SCENE_ID, "used": 0}}
    cache = SceneCache({"slots": {str(GROUP): slots, "1": other}}, Mock())
    assert cache.allocate(GROUP, "custom:new") == LAST_SCENE_ID
    assert cache.slot(1, "custom:old") is None


def test_cache_state_round_trips_and_saves() -> None:
    save = Mock()
    cache = SceneCache({}, save)
    cache.allocate(GROUP, "custom:a")
    cache.set_stored(lamp(1).ieee, 0x80, CONTENT_A)
    assert save.call_count == 2
    restored = SceneCache(cache.as_dict(), Mock())
    assert restored.slot(GROUP, "custom:a") == 0x80
    assert restored.stored(lamp(1).ieee, 0x80) == CONTENT_A


def _engine() -> tuple[FakeTransport, SceneCache, SceneEngine]:
    transport = FakeTransport()
    cache = SceneCache({}, Mock())
    return transport, cache, SceneEngine(transport, cache)
