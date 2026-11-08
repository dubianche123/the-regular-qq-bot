"""Screen-edge contact is held by the authored support, not a moving feet anchor."""
import random
from unittest.mock import patch

import pytest

from regular.pet_brain import CLIPS, PetBrain


AWAKE = {
    "left": "motion_edge_look", "right": "motion_edge_look",
    "bottom": "motion_bottom_look", "top": "boundary_top_look",
}
SLEEP = {
    "left": "boundary_side_sleep", "right": "boundary_side_sleep",
    "bottom": "boundary_bottom_sleep", "top": "boundary_top_sleep",
}


def hidden(edge, *, scale=1.0):
    brain = PetBrain(rng=random.Random(7), now=0, scale=scale)
    brain.set_ground(-800, 640, 860, 38)
    brain.place(300)
    brain.hidden_edge, brain.mode = edge, "hidden"
    brain.bottom_peek, brain.top_peek = edge == "bottom", edge == "top"
    brain.facing = -1 if edge == "right" else 1
    brain._hidden_side_y = 400 if edge in ("left", "right") else None
    brain._hidden_clip = brain._hidden_awake_clip = AWAKE[edge]
    brain._set_clip(AWAKE[edge], 0, restart=True)
    brain.x, brain.y = brain._hidden_target()
    return brain


def assert_support(brain, edge, clip):
    geometry = brain.repertoire.actions[clip]["geometry"]
    if edge in ("left", "right"):
        contact = brain.x + geometry["rest_x"] * brain.figure / 320 * brain.facing
        plane = brain.left if edge == "left" else brain.right
    else:
        contact = brain.y + geometry["rest_y"] * brain.figure / 320
        plane = brain.floor if edge == "bottom" else brain.ceiling
    assert contact == pytest.approx(plane)


@pytest.mark.parametrize("edge", AWAKE)
@pytest.mark.parametrize("scale", (0.6, 1.0, 1.5))
def test_sleep_and_wake_keep_the_real_support_attached(edge, scale):
    brain = hidden(edge, scale=scale)
    brain._hidden_sleep_at = 1
    # An outgoing ground-pose dissolve must not follow the new support anchor.
    brain._fade = ("idle", 0, False, 0.9, 0.5)
    with patch.object(brain, "_pick", return_value=SLEEP[edge]):
        brain.tick(1)
    assert brain.clip == SLEEP[edge]
    assert brain.pose(1)[3]["fade"] is None
    for time in (1, 1.2, 1.4, 1.8, 2.2, 4, 8):
        brain.set_ground(-800, 640, 860, 38)
        brain.tick(time)
        assert_support(brain, edge, SLEEP[edge])
    settled = (brain.x, brain.y)
    brain.poke(9)
    frames = []
    for time in (9, 9.21, 9.41):
        brain.set_ground(-800, 640, 860, 38)
        brain.tick(time)
        assert brain.clip == SLEEP[edge]
        assert (brain.x, brain.y) == pytest.approx(settled)
        assert_support(brain, edge, SLEEP[edge])
        frames.append(brain.pose(time)[1])
    assert frames == [2, 1, 0]
    brain.tick(9.61)
    assert brain._edge_exit["phase"] == "prepare"
    assert not brain.hidden_asleep


@pytest.mark.parametrize("edge", ("top", "bottom"))
@pytest.mark.parametrize("sleeping", (False, True))
def test_hidden_horizontal_edges_stay_in_the_current_screen_after_resize(edge, sleeping):
    brain = hidden(edge)
    if sleeping:
        brain.hidden_asleep = True
        brain._hidden_clip = SLEEP[edge]
        brain._set_clip(SLEEP[edge], 0, restart=True)
    for scale, bounds in ((1.5, (-1600, -900, 700, 24)), (0.6, (200, 700, 900, 40))):
        brain.scale = scale
        brain.set_ground(*bounds)
        assert brain.left + brain.figure * .35 <= brain.x <= brain.right - brain.figure * .35
        assert (brain.mode, brain.hidden_edge) == ("hidden", edge)
        if sleeping:
            assert_support(brain, edge, SLEEP[edge])


@pytest.mark.parametrize("edge", AWAKE)
def test_old_assets_wake_with_a_steady_acknowledging_gaze(edge):
    brain = hidden(edge)
    brain.hidden_asleep = True
    brain.poke(1)
    assert brain._edge_exit["phase"] == "wake"
    for time in (1, 1.1, 1.2):
        brain.tick(time)
        assert brain.pose(time)[1] == 1
    brain.tick(1.31)
    assert brain._edge_exit["phase"] == "prepare"


def test_waking_is_interruptible_by_drag_without_replaying_sleep_entry():
    brain = hidden("left")
    brain._hidden_sleep_at = 1
    with patch.object(brain, "_pick", return_value=SLEEP["left"]):
        brain.tick(1)
    brain.poke(3)
    brain.tick(3.21)
    brain.press(3.22, brain.x, brain.y - 50)
    brain.drag(3.3, brain.x + 60, brain.y - 80)
    assert brain.mode == "drag"
    assert brain._edge_exit is None
    assert brain.hidden_edge is None
    assert not brain.hidden_asleep


def adopt_edge_assets(brain, monkeypatch):
    """Exercise new state transitions before raster sheets are installed."""
    from regular.pet_actions_edge_daily import ACTIONS
    for name, original in ACTIONS.items():
        spec = dict(original)
        geometry = ({'rest_x': -41.4} if 'grip_x' in spec else
                    {'rest_y': -100.0 if spec['family'] == 'bottom' else -270.0}
                    if 'rest_y' in spec else {})
        spec['geometry'] = geometry
        monkeypatch.setitem(brain.repertoire.actions, name, spec)
        monkeypatch.setitem(CLIPS, name, (name, spec.get('sequence', [0, 1, 2, 3]), spec['fps'], False, spec['posture']))


@pytest.mark.parametrize('edge,clip', (
    ('left', 'edge_daily_side_tail_greet'), ('right', 'edge_daily_side_listen'),
    ('bottom', 'edge_daily_bottom_watch'), ('bottom', 'edge_daily_bottom_yawn'),
    ('top', 'edge_daily_top_wonder'),
))
def test_micro_performance_keeps_support_then_returns_without_restarting_sleep(edge, clip, monkeypatch):
    brain = hidden(edge)
    adopt_edge_assets(brain, monkeypatch)
    brain._hidden_micro_at, brain._hidden_sleep_at = 1, 90
    with patch.object(brain, '_pick', return_value=clip):
        brain.tick(1)
    assert brain.clip == clip
    for time in (1.2, 1.4, 1.6, 1.8):
        brain.set_ground(-800, 640, 860, 38)
        brain.tick(time)
        assert_support(brain, edge, clip)
    brain.tick(4)
    assert brain.clip == AWAKE[edge]
    assert brain._hidden_sleep_at == 90
    assert brain._hidden_micro_at >= 46
    assert brain.mode == 'hidden'


@pytest.mark.parametrize('quiet,motion', ((True, True), (False, False)))
def test_micro_performance_respects_quiet_and_reduced_motion(quiet, motion, monkeypatch):
    brain = hidden('bottom')
    adopt_edge_assets(brain, monkeypatch)
    brain.quiet, brain.motion = quiet, motion
    brain._hidden_micro_at, brain._hidden_sleep_at = 1, 90
    brain.tick(1)
    assert brain.clip == AWAKE['bottom']


@pytest.mark.parametrize('new_art', (False, True))
def test_top_escape_opens_its_route_while_supported_without_ground_fear(new_art, monkeypatch):
    brain = hidden('top')
    if new_art:
        adopt_edge_assets(brain, monkeypatch)
    else:
        for name in ('edge_daily_top_cast', 'edge_daily_top_release'):
            monkeypatch.delitem(brain.repertoire.actions, name, raising=False)
    brain.poke(1)
    clips, phases = [], []
    for i in range(1, 420):
        now = 1 + i / 60
        brain.set_ground(-800, 640, 860, 38)
        brain.tick(now)
        clips.append(brain.clip)
        if brain.mode == 'edge_exit':
            assert brain._edge_exit['phase'] == 'prepare'
            assert brain.y < brain.floor
            if new_art:
                assert_support(brain, 'top', 'edge_daily_top_cast')
        if brain.warp:
            phases.append(brain.warp['phase'])
            if brain.warp['phase'] == 'top_release':
                assert brain.warp['src'] is not None
                assert brain.warp['dst'] is not None
                assert brain.y < brain.floor
        if brain.mode == 'ground':
            break
    assert not any(clip.startswith('scene_height_') for clip in clips)
    assert 'boundary_top_return' not in clips
    assert phases.index('top_sink') < phases.index('gone') < phases.index('rise')
    assert (brain.mode, brain.y) == ('ground', brain.floor)
    if new_art:
        assert phases.index('top_release') < phases.index('top_sink')
        assert 'edge_daily_top_cast' in clips
        assert 'edge_daily_top_release' in clips


def test_portal_stumble_only_starts_after_landing_and_never_on_consecutive_trips(monkeypatch):
    from regular.pet_brain import Step
    brain = PetBrain(rng=random.Random(7), now=0)
    brain.set_ground(0, 1440, 860, 38)
    brain.place(300)
    adopt_edge_assets(brain, monkeypatch)
    landings = []
    for start, target in ((0, 1100), (12, 300)):
        brain._warp_request = {'x': target, 'via': 'floor', 'cause': 'spell'}
        brain._queue(Step('warp_cast'), Step('idle', 2), now=start, interrupt=True)
        landing_frames = []
        with patch.object(brain.rng, 'random', return_value=0.0):
            for i in range(1, 601):
                now = start + i / 60
                brain.tick(now)
                if brain.warp and brain.warp['phase'] == 'land':
                    landing_frames.append((brain.clip, brain.pose(now)[1]))
                    assert brain.y == brain.floor
                    assert brain.portal_clip is None
                    assert brain.x == target
                if brain.warp is None:
                    break
        landings.append(landing_frames)
        assert brain.warp is None
    assert {clip for clip, _ in landings[0]} == {'edge_daily_warp_stumble'}
    assert {frame for _, frame in landings[0]} == {0, 1, 2, 3}
    assert {clip for clip, _ in landings[1]} == {'warp_land'}


@pytest.mark.parametrize('scale', (0.6, 1.0, 1.5))
def test_awake_top_contact_keeps_the_shoes_visible_before_casting(scale):
    brain = hidden('top', scale=scale)
    geometry = brain.repertoire.actions['boundary_top_look']['geometry']
    assert geometry['rest_y'] == geometry['bounds'][1]
    assert_support(brain, 'top', 'boundary_top_look')
    # The highest visible pixel is the inverted shoe tip, not a cropped leg.
    assert brain.y + geometry['bounds'][1] * brain.figure / 320 == pytest.approx(brain.ceiling)


def test_top_release_stays_attached_then_descends_before_the_head_turns_upright(monkeypatch):
    brain = hidden('top')
    adopt_edge_assets(brain, monkeypatch)
    geometry = brain.repertoire.actions['edge_daily_top_release']['geometry']
    geometry['bounds'] = [-100, -320, 100, 0]
    brain._begin_top_escape(1)
    origin = brain.y
    for time in (1, 1.1, 1.199):
        brain._warp(time, 1 / 60)
        assert brain.y == origin
        assert_support(brain, 'top', 'edge_daily_top_release')
    brain._warp(1.3, 1 / 60)
    assert origin < brain.y < brain.warp['release_y']
    for time in (1.4, 1.6, 1.79):
        brain._warp(time, 1 / 60)
        assert brain.y - brain._head_clearance('edge_daily_top_release') >= brain.ceiling
        assert brain.transform(time)['sx'] == brain.transform(time)['sy'] == 1
    brain._warp(1.81, 1 / 60)
    assert brain.warp['phase'] == 'top_sink'
    assert brain.warp['origin_y'] == pytest.approx(brain.warp['release_y'])
