"""Hidden poses expose the face; exits open a route before releasing support."""
import random
from unittest.mock import patch

import pytest

from regular.pet_brain import CLIPS, PetBrain


AWAKE = {
    "left": "motion_edge_look", "right": "motion_edge_look",
    "bottom": "motion_bottom_look", "top": "boundary_top_look",
}
SLEEP = {
    "left": "boundary_side_doze", "right": "boundary_side_doze",
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


def sleeping_hidden(edge):
    brain = hidden(edge)
    brain._hidden_sleep_at = 1
    with patch.object(brain, "_pick", return_value=SLEEP[edge]):
        brain.tick(1)
    assert brain.hidden_asleep
    return brain


def finish_waking(brain, since=2, seconds=12):
    shown = []
    for frame in range(int(seconds * 30)):
        brain.tick(since + frame / 30)
        shown.append((brain.mode, brain.clip))
    return shown


@pytest.mark.parametrize("edge", AWAKE)
@pytest.mark.parametrize("kind", ("mention", "named", "quoted", "called"))
def test_direct_group_call_wakes_hidden_sleep_once_and_then_greets(edge, kind):
    brain = sleeping_hidden(edge)
    anchor = (brain.x, brain.y)
    event = {"kind": kind, "ts": 100, "id": "synthetic-call"}
    brain.observe(2, {"bot": "ready", "events": [event]})
    assert brain.mode == "edge_exit"
    assert brain._edge_exit["phase"] == "wake"
    assert not brain.hidden_asleep
    assert (brain.x, brain.y) == pytest.approx(anchor)
    phase_started = brain._edge_exit["since"]
    brain.observe(2.1, {"bot": "ready", "events": [event]})
    brain.observe(2.2, {"bot": "ready", "events": [{**event, "id": "new-call"}]})
    assert brain._edge_exit["since"] == phase_started
    shown = finish_waking(brain)
    assert any(mode == "ground" and brain._role(clip) == "greet" for mode, clip in shown)
    assert brain.mode == "ground"
    assert brain.hidden_edge is None
    assert not brain.hidden_asleep


@pytest.mark.parametrize("edge", AWAKE)
@pytest.mark.parametrize("name", ("listen", "think", "talk", "reply"))
def test_local_conversation_wakes_hidden_sleep_and_keeps_current_state(edge, name):
    brain = sleeping_hidden(edge)
    brain.set_state(name, 2, source="local")
    assert brain.mode == "edge_exit"
    assert not brain.hidden_asleep
    shown = finish_waking(brain)
    assert any(mode == "ground" and (clip == name or brain._role(clip) == name)
               for mode, clip in shown)
    assert brain.state_name == name
    assert brain.state_source == "local"
    assert brain.mode in ("ground", "fly")
    assert not brain.hidden_asleep


@pytest.mark.parametrize("edge", AWAKE)
def test_same_local_listening_state_still_wakes_a_hidden_sleeper(edge):
    brain = sleeping_hidden(edge)
    brain.state_name = brain.state_clip = "listen"
    brain.state_source, brain.state_until = "local", float("inf")
    brain.set_state("listen", 2, source="local")
    started = brain._edge_exit["since"]
    brain.set_state("listen", 2.1, source="local")
    assert brain._edge_exit["since"] == started
    assert not brain.hidden_asleep
    shown = finish_waking(brain)
    assert any(mode == "ground" and (clip == "listen" or brain._role(clip) == "listen")
               for mode, clip in shown)


@pytest.mark.parametrize("edge", AWAKE)
def test_local_chat_sent_wakes_and_waits_for_reply_after_exit(edge):
    brain = sleeping_hidden(edge)
    brain.chat_sent(2)
    assert brain.mode == "edge_exit"
    assert brain.chat_pending
    shown = finish_waking(brain)
    assert any(mode == "ground" and clip == "think" for mode, clip in shown)
    assert brain.chat_pending
    assert brain.step.hold == "chat"
    brain.chat_reply(14, "听到啦")
    assert not brain.chat_pending
    assert brain.state_name == "reply"
    assert not brain.hidden_asleep


@pytest.mark.parametrize("edge", AWAKE)
def test_unrelated_heartbeat_does_not_wake_hidden_sleep(edge):
    brain = sleeping_hidden(edge)
    for now in (2, 3, 4):
        brain.observe(now, {"bot": "ready", "state": None, "chatter": 30, "events": []})
        brain.tick(now)
    assert brain.mode == "hidden"
    assert brain.hidden_asleep


@pytest.mark.parametrize("edge", AWAKE)
def test_reply_arriving_during_wake_owns_the_pose_and_actual_words(edge):
    brain = sleeping_hidden(edge)
    brain.chat_sent(2)
    started = brain._edge_exit["since"]
    brain.chat_reply(2.1, "我听到你啦")
    brain.set_state("talk", 2.1, source="local")
    brain.say("我听到你啦", now=2.1, seconds=30)
    assert brain._edge_exit["since"] == started
    shown = finish_waking(brain)
    assert not brain.chat_pending
    assert brain.state_name == "talk"
    assert any(mode == "ground" and clip == "talk" for mode, clip in shown)
    assert brain.bubble.text == "我听到你啦"


def test_same_listen_state_wakes_a_ground_sleeper():
    brain = PetBrain(rng=random.Random(7), now=0)
    brain._set_clip("sleep", 0, restart=True)
    brain.state_name = brain.state_clip = "listen"
    brain.state_source, brain.state_until = "local", float("inf")
    brain.set_state("listen", 2, source="local")
    assert brain.clip == "wake"
    assert any(step.clip == "listen" or brain._role(step.clip) == "listen" for step in brain.plan)


def assert_support(brain, edge, clip):
    geometry = brain.repertoire.actions[clip]["geometry"]
    if edge in ("left", "right"):
        contact = brain.x + geometry["rest_x"] * brain.figure / 320 * brain.facing
        plane = brain.left if edge == "left" else brain.right
    else:
        contact = brain.y + geometry["rest_y"] * brain.figure / 320
        plane = brain.floor if edge == "bottom" else brain.ceiling
    assert contact == pytest.approx(plane)


def assert_hidden_projection(brain, edge, clip):
    geometry = brain.repertoire.actions[clip]["geometry"]
    factor = brain.figure / 320
    bounds = geometry.get("bounds", [-100, -300, 100, 0])
    if edge == "top":
        visible_depth = brain.y + bounds[3] * factor - brain.ceiling
        assert brain.figure * .25 < visible_depth < brain.figure * .5
        assert brain.y + bounds[1] * factor < brain.ceiling
    elif edge in ("left", "right") and "rest_x" not in geometry:
        left = brain.x + (bounds[0] if brain.facing > 0 else -bounds[2]) * factor
        right = brain.x + (bounds[2] if brain.facing > 0 else -bounds[0]) * factor
        assert left < (brain.left if edge == "left" else brain.right) < right
    else:
        assert_support(brain, edge, clip)


@pytest.mark.parametrize("edge", AWAKE)
@pytest.mark.parametrize("scale", (0.6, 1.0, 1.5))
def test_sleep_and_wake_keep_the_hidden_pose_without_exposing_the_body(edge, scale):
    brain = hidden(edge, scale=scale)
    brain._hidden_sleep_at = 1
    awake_anchor = (brain.x, brain.y)
    # An outgoing ground-pose dissolve must not follow the new support anchor.
    brain._fade = ("idle", 0, False, 0.9, 0.5)
    with patch.object(brain, "_pick", return_value=SLEEP[edge]):
        brain.tick(1)
    assert brain.clip == SLEEP[edge]
    assert brain.pose(1)[3]["fade"] is None
    for time in (1, 1.2, 1.4, 1.8, 2.2, 4, 8):
        brain.set_ground(-800, 640, 860, 38)
        brain.tick(time)
        assert_hidden_projection(brain, edge, SLEEP[edge])
    settled = (brain.x, brain.y)
    if edge in ("left", "right"):
        assert settled == awake_anchor
    entry = brain.repertoire.actions[SLEEP[edge]]["entry_sequence"]
    brain.poke(9)
    frames = []
    for index in range(len(entry)):
        time = 9 + index / 5 + .01
        brain.set_ground(-800, 640, 860, 38)
        brain.tick(time)
        assert brain.clip == SLEEP[edge]
        assert (brain.x, brain.y) == pytest.approx(settled)
        assert_hidden_projection(brain, edge, SLEEP[edge])
        frames.append(brain.pose(time)[1])
    assert frames == list(reversed(entry))
    brain.tick(9 + len(entry) / 5 + .01)
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
            assert_hidden_projection(brain, edge, SLEEP[edge])


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
def test_micro_performance_keeps_hidden_projection_then_returns_without_restarting_sleep(edge, clip, monkeypatch):
    brain = hidden(edge)
    adopt_edge_assets(brain, monkeypatch)
    brain._hidden_micro_at, brain._hidden_sleep_at = 1, 90
    with patch.object(brain, '_pick', return_value=clip):
        brain.tick(1)
    assert brain.clip == clip
    for time in (1.2, 1.4, 1.6, 1.8):
        brain.set_ground(-800, 640, 860, 38)
        brain.tick(time)
        assert_hidden_projection(brain, edge, clip)
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
            assert_hidden_projection(brain, 'top', brain.clip)
            assert len(brain.portals) == 2
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
@pytest.mark.parametrize('clip', ('boundary_top_look', 'boundary_top_sleep', 'edge_daily_top_wonder'))
def test_top_hidden_exposes_only_a_head_depth_and_keeps_shoes_outside(scale, clip):
    brain = hidden('top', scale=scale)
    brain._hidden_clip = clip
    brain.x, brain.y = brain._hidden_target()
    geometry = brain.repertoire.actions[clip]['geometry']
    assert_hidden_projection(brain, 'top', clip)
    assert brain.y + geometry['rest_y'] * brain.figure / 320 < brain.ceiling


def test_top_release_stays_attached_then_descends_before_the_head_turns_upright(monkeypatch):
    brain = hidden('top')
    adopt_edge_assets(brain, monkeypatch)
    geometry = brain.repertoire.actions['edge_daily_top_release']['geometry']
    geometry['bounds'] = [-100, -320, 100, 0]
    brain.poke(1)
    brain.tick(2.34)
    assert brain.warp['phase'] == 'top_release'
    started = brain.warp['since']
    origin = brain.y
    for elapsed in (0, .1, .199):
        time = started + elapsed
        brain._warp(time, 1 / 60)
        assert brain.y == origin
        assert_hidden_projection(brain, 'top', 'edge_daily_top_release')
        assert brain.warp['src'].openness(time) == 1
    brain._warp(started + .3, 1 / 60)
    assert origin < brain.y < brain.warp['release_y']
    for elapsed in (.4, .6, .79):
        time = started + elapsed
        brain._warp(time, 1 / 60)
        assert brain.y - brain._head_clearance('edge_daily_top_release') >= brain.ceiling
        assert brain.transform(time)['sx'] == brain.transform(time)['sy'] == 1
    brain._warp(started + .81, 1 / 60)
    assert brain.warp['phase'] == 'top_sink'
    assert brain.warp['origin_y'] == pytest.approx(brain.warp['release_y'])


@pytest.mark.parametrize('edge', ('left', 'right'))
def test_side_sleep_closes_the_same_peeking_eyes_and_never_slips_into_full_body(edge):
    brain = hidden(edge)
    awake_anchor = (brain.x, brain.y)
    brain._hidden_sleep_at = 1
    brain.tick(1)
    assert brain.clip == 'boundary_side_doze'
    assert CLIPS[brain.clip][0] == CLIPS[AWAKE[edge]][0]
    assert not brain.repertoire.actions[brain.clip].get('slip_clip')
    with patch.object(brain.rng, 'random', return_value=0.0):
        for time in (3, 91, 112, 612, 700):
            brain.tick(time)
            assert brain.clip == 'boundary_side_doze'
            assert brain.pose(time)[0:2] == ('motion_edge_look', 3)
            assert (brain.x, brain.y) == awake_anchor
            assert brain._hidden_slip_started is None


@pytest.mark.parametrize('scale', (0.6, 1.0, 1.5))
@pytest.mark.parametrize('sleeping', (False, True))
def test_top_cast_stays_at_the_original_head_depth_until_the_open_route_is_ready(scale, sleeping):
    brain = hidden('top', scale=scale)
    if sleeping:
        brain._hidden_sleep_at = .1
        with patch.object(brain, '_pick', return_value=SLEEP['top']):
            brain.tick(.1)
        brain.tick(.9)
    geometry = brain.repertoire.actions[brain.clip]['geometry']
    hidden_bottom = brain.y + geometry['bounds'][3] * brain.figure / 320
    brain.poke(1)
    now = 1
    while brain._edge_exit['phase'] == 'wake':
        now += .05
        brain.tick(now)
    started = brain._edge_exit['since']
    assert brain.clip == 'edge_daily_top_cast'
    geometry = brain.repertoire.actions[brain.clip]['geometry']
    assert brain.y + geometry['bounds'][3] * brain.figure / 320 == pytest.approx(hidden_bottom)
    assert_hidden_projection(brain, 'top', brain.clip)
    route = tuple(brain.portals)
    assert len(route) == 2 and route[0].born == started
    positions, frames = [], set()
    for elapsed in (0, .1, .2, .34, .5, .67, .9, 1.01, 1.3):
        time = started + elapsed
        brain.tick(time)
        positions.append(brain.y)
        frames.add(brain.pose(time)[1])
        assert brain.clip == 'edge_daily_top_cast'
        assert brain.warp is None
        assert_hidden_projection(brain, 'top', brain.clip)
        assert tuple(brain.portals) == route
        if elapsed >= .65:
            assert all(portal.openness(time) == 1 for portal in route)
    assert positions == pytest.approx([positions[0]] * len(positions))
    assert frames == {0, 1, 2, 3}
    brain.tick(started + 1.34)
    assert brain.warp['phase'] == 'top_release'
    assert brain.warp['src'] is route[0] and brain.warp['dst'] is route[1]
    geometry = brain.repertoire.actions[brain.clip]['geometry']
    assert brain.y + geometry['bounds'][3] * brain.figure / 320 == pytest.approx(hidden_bottom)
    origin = brain.y
    brain.tick(started + 1.5)
    assert brain.y == origin


def test_drag_during_top_cast_closes_the_preopened_route():
    brain = hidden('top')
    brain.poke(1)
    portals = tuple(brain.portals)
    brain.press(1.1, brain.x, brain.y)
    brain.drag(1.2, brain.x + 30, brain.y + 30)
    assert brain.mode == 'drag'
    assert all(portal.closing == 1.2 for portal in portals)
    brain.tick(1.8)
    assert not brain.portals


def test_cancelling_before_the_destination_opens_never_flashes_it_later():
    brain = hidden('top')
    brain.poke(1)
    source, destination = brain.portals
    brain.press(1.02, brain.x, brain.y)
    brain.drag(1.05, brain.x + 30, brain.y + 30)
    initial = source.openness(1.05)
    for time in (1.06, 1.15, 1.2, 1.3, 1.55):
        assert destination.openness(time) == 0
        assert source.openness(time) <= initial


def test_top_route_follows_screen_and_scale_changes_without_reopening():
    brain = hidden('top')
    brain.poke(1)
    portals = tuple(brain.portals)
    born = [portal.born for portal in portals]
    brain.scale = 1.5
    brain.set_ground(-700, 500, 700, 50)
    brain.tick(1.2)
    assert tuple(brain.portals) == portals
    assert [portal.born for portal in portals] == born
    assert all(portal.x == brain.x and portal.radius == brain.figure * .5 for portal in portals)
    assert portals[0].y == pytest.approx(brain.ceiling + brain.figure * .65)
    assert portals[1].y == brain.floor
    assert_hidden_projection(brain, 'top', brain.clip)


@pytest.mark.parametrize('disable_after_open', (False, True))
def test_top_exit_without_motion_returns_without_leaving_an_open_route(disable_after_open):
    brain = hidden('top')
    brain.motion = disable_after_open
    brain.poke(1)
    portals = tuple(brain.portals)
    brain.motion = False
    for index in range(1, 301):
        brain.tick(1 + index / 60)
        if brain.mode == 'ground':
            break
    assert brain.mode == 'ground' and brain.y == brain.floor
    assert brain.warp is None and not brain.portals
    if disable_after_open:
        assert all(portal.closing is not None for portal in portals)
