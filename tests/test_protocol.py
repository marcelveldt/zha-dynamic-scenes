"""Frame encoding against frames captured from a Hue bridge."""

import pytest

from custom_components.zha_dynamic_scenes.protocol import (
    SceneContent,
    add_scene_payload,
    dynamic_palette_frame,
    dynamic_palette_payload,
    pack_palette_xy,
    remove_scene_payload,
    zcl_frame,
)

# palette colours of Hue gallery scenes, in the order of the bridge's palette
BLOSSOM = [
    (0.3971999, 0.4024999),
    (0.24, 0.2761),
    (0.4418999, 0.4164),
    (0.2674, 0.3018),
    (0.5118999, 0.4249),
]
NACHTS = [
    (0.5818872, 0.3812814),
    (0.6583429, 0.3021202),
    (0.6449486, 0.3287721),
    (0.6181662, 0.3520075),
    (0.5374619, 0.4040466),
]
GIPSKRUID = [
    (0.3453173, 0.3188527),
    (0.2986419, 0.2966608),
    (0.2850921, 0.3300249),
    (0.3090207, 0.3781798),
    (0.3456131, 0.3928372),
]
SPEED_GALLERY = 0.6269841


@pytest.mark.parametrize(
    ("scene_id", "colors", "speed", "brightness", "transition", "expected"),
    [
        (
            0x6D,
            BLOSSOM,
            SPEED_GALLERY,
            None,
            None,
            "00006d0c1350000000a5887b39b5549fc97fd2955c256b82a0",
        ),
        (
            0x6D,
            BLOSSOM,
            SPEED_GALLERY,
            0x34,
            None,
            "00006d0e341350000000a5887b39b5549fc97fd2955c256b82a0",
        ),
        (
            0x6D,
            BLOSSOM,
            SPEED_GALLERY,
            None,
            30,
            "00006d0d1e001350000000a5887b39b5549fc97fd2955c256b82a0",
        ),
        (
            0x6D,
            BLOSSOM,
            SPEED_GALLERY,
            0x80,
            10,
            "00006d0f0a00801350000000a5887b39b5549fc97fd2955c256b82a0",
        ),
        (
            0x71,
            NACHTS,
            SPEED_GALLERY,
            None,
            None,
            "0000710c1350000000abfc7455be5c0aee64750d6cb3fb7ba0",
        ),
        (
            0x6C,
            GIPSKRUID,
            0.7301587,
            None,
            None,
            "00006c0c135000000084d76180065b354665ba0674868778ba",
        ),
    ],
)
def test_dynamic_palette_payload_matches_bridge(
    scene_id: int,
    colors: list[tuple[float, float]],
    speed: float,
    brightness: int | None,
    transition: int | None,
    expected: str,
) -> None:
    payload = dynamic_palette_payload(
        0x0000, scene_id, colors, speed=speed, brightness=brightness, transition=transition
    )
    assert payload.hex() == expected


def test_dynamic_palette_frame_header() -> None:
    payload = dynamic_palette_payload(0x0000, 0x6D, BLOSSOM, speed=SPEED_GALLERY)
    frame = dynamic_palette_frame(0x2A, payload)
    assert frame == bytes.fromhex("150b102a00") + payload


def test_dynamic_palette_group_and_optional_speed() -> None:
    payload = dynamic_palette_payload(0xDD92, 0x80, BLOSSOM[:2])
    # group, scene, flags (palette only), block size, count << 4, style, reserved, colours
    assert payload.hex() == "92dd80" + "04" + "0a" + "20" + "00" + "0000" + "a5887b39b554"


@pytest.mark.parametrize("count", [0, 10])
def test_dynamic_palette_rejects_palette_size(count: int) -> None:
    with pytest.raises(ValueError, match="colours"):
        dynamic_palette_payload(0, 0x80, [(0.3, 0.3)] * count)


def test_pack_palette_xy_saturates() -> None:
    assert pack_palette_xy(0.8, 0.9) == bytes([0xFF, 0xFF, 0xFF])


def test_add_scene_payload() -> None:
    content = SceneContent(level=0xCB, x=0x1234, y=0x5678)
    assert (
        add_scene_payload(0xDD92, 0x80, content, 4).hex()
        == (
            "92dd80" + "0400" + "00"  # group, scene, transition, empty name
            "0600" + "01" + "01"  # on/off: on
            "0800" + "01" + "cb"  # level
            "0003" + "04" + "3412" + "7856"  # colour: current x, current y
        )
    )


def test_add_scene_frame() -> None:
    payload = add_scene_payload(0x0001, 0x80, SceneContent(1, 0, 0), 0)
    assert zcl_frame(0x07, 0x00, payload) == bytes.fromhex("010700") + payload


def test_remove_scene_payload() -> None:
    assert remove_scene_payload(0xDD92, 0x80).hex() == "92dd80"


@pytest.mark.parametrize(
    ("xy", "brightness", "expected"),
    [
        ((0.5, 0.25), 100, SceneContent(level=254, x=32768, y=16384)),
        ((0.5, 0.25), 0, SceneContent(level=1, x=32768, y=16384)),
        ((0.3972, 0.4025), 80, SceneContent(level=204, x=26031, y=26378)),
        # levels the Hue bridge sent for these percentages
        ((0.5, 0.25), 20, SceneContent(level=0x34, x=32768, y=16384)),
        ((0.5, 0.25), 50, SceneContent(level=0x80, x=32768, y=16384)),
    ],
)
def test_scene_content_from_xy(
    xy: tuple[float, float], brightness: float, expected: SceneContent
) -> None:
    assert SceneContent.from_xy(xy, brightness) == expected
