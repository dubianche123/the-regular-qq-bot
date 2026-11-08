"""Object-specific, quiet-first endings for local desktop accompaniment."""
import random
from unittest.mock import Mock, patch

import pytest

from regular.pet_actions_endings import ACTIONS as ENDINGS
from regular.pet_brain import CLIPS, PetBrain, Step
from regular.pet_repertoire import ACTIONS, clips, curated_actions
from regular.pet_routines import ACTIVITY_ENDINGS, activity_sequence, choose_routine


def catalog():
    return {**ACTIONS, **ENDINGS}


def sequence(clip, *, draws=(0, 0), actions=None, duration=60, **context):
    rng = Mock()
    rng.random.side_effect = draws
    return activity_sequence(rng, catalog() if actions is None else actions, clip, duration, **context)


@pytest.mark.parametrize('clip', ACTIVITY_ENDINGS)
def test_exact_object_keeps_main_then_one_result_and_one_cleanup(clip):
    first, second, cleanup = ACTIVITY_ENDINGS[clip]
    for draw, expected in ((0, first), (.999, second)):
        beats = sequence(clip, draws=(0, draw), energy=.3, sleepy=True)
        assert beats == [(clip, 60), (expected, None), (cleanup, None)]
        assert ENDINGS[expected]['starts_from'] == ACTIONS[clip]['posture']
        assert ENDINGS[cleanup]['posture'] == ACTIONS[clip]['posture']
        assert not ENDINGS[expected]['loop'] and not ENDINGS[cleanup]['loop']
    assert sequence(clip, draws=(.99,)) == [(clip, 60), (cleanup, None)]


def test_endings_cannot_be_selected_as_an_ambient_or_state_activity():
    assert curated_actions(ENDINGS) == {}
    assert {spec['role'] for spec in ENDINGS.values()} == {'activity_ending'}
    assert choose_routine(random.Random(1), ENDINGS, mode='rest') is None
    assert choose_routine(random.Random(1), ENDINGS, mode='story') is None


@pytest.mark.parametrize('clip', ACTIVITY_ENDINGS)
def test_missing_cleanup_keeps_main_and_missing_results_still_put_away(clip):
    first, second, cleanup = ACTIVITY_ENDINGS[clip]
    actions = catalog()
    del actions[cleanup]
    assert sequence(clip, actions=actions, draws=()) == [(clip, 60)]
    actions = catalog()
    del actions[first]
    del actions[second]
    assert sequence(clip, actions=actions) == [(clip, 60), (cleanup, None)]


def test_missing_resolve_art_does_not_promote_the_rare_sour_note():
    actions = catalog()
    del actions['ending_ukulele_resolve']
    beats = sequence('dragon_music_ukulele', draws=(0, .1), actions=actions, energy=.2)
    assert beats == [('dragon_music_ukulele', 60), ('ending_ukulele_put_away', None)]


@pytest.mark.parametrize('clip', ('laptop', 'life_search_laptop', 'life_reply_sit_laptop',
                                  'headphones', 'dragon_music_harmonica'))
def test_actual_work_listening_and_other_instruments_do_not_borrow_endings(clip):
    assert sequence(clip, draws=()) == [(clip, 60)]


def test_strings_family_does_not_imply_matching_instruments():
    actions = catalog()
    actions['another_instrument'] = dict(ACTIONS['dragon_music_ukulele'])
    assert sequence('another_instrument', actions=actions, draws=()) == [('another_instrument', 60)]
    assert ACTIVITY_ENDINGS['life_laptop_type'] == ACTIVITY_ENDINGS['life_laptop_think']
    assert set(ACTIVITY_ENDINGS['dragon_music_ukulele']).isdisjoint(ACTIVITY_ENDINGS['dragon_music_kalimba'])


def test_quiet_and_state_conditions_apply_before_result_selection():
    assert len(sequence('dragon_music_ukulele', draws=(.2, 0))) == 3
    assert len(sequence('dragon_music_ukulele', draws=(.2,), quiet=True)) == 2
    for context in ({}, {'quiet': True, 'energy': .1}, {'quiet': True, 'mood': -.8}):
        assert sequence('dragon_music_ukulele', draws=(0, .999), **context)[1][0] == 'ending_ukulele_resolve'
    assert sequence('dragon_music_ukulele', draws=(0, .999), mood=-.4)[1][0] == 'ending_ukulele_sour'
    assert sequence('dragon_music_kalimba', draws=(0, .999))[1][0] == 'ending_kalimba_linger'
    for context in ({'sleepy': True}, {'energy': .2}, {'quiet': True, 'sleepy': True}):
        assert sequence('dragon_music_kalimba', draws=(0, .999), **context)[1][0] == 'ending_kalimba_drowsy'


def test_work_pauses_when_tired_and_settles_more_easily_in_a_good_mood():
    assert sequence('life_laptop_type', draws=(0, .5), energy=1)[1][0] == 'ending_work_clear'
    assert sequence('life_laptop_type', draws=(0, .5), energy=.1)[1][0] == 'ending_work_pause'
    assert sequence('life_laptop_think', draws=(0, .8), mood=.8)[1][0] == 'ending_work_clear'
    assert sequence('life_laptop_think', draws=(0, .8), mood=-.8)[1][0] == 'ending_work_pause'


@pytest.mark.parametrize('clip', ACTIVITY_ENDINGS)
def test_repeat_suppression_never_promotes_a_rare_result(clip):
    first, second, cleanup = ACTIVITY_ENDINGS[clip]
    context = dict(energy=.3, sleepy=True)
    assert sequence(clip, recent_clips=(first, cleanup, clip), **context) == [(clip, 60), (cleanup, None)]
    assert sequence(clip, recent_clips=(second, cleanup, clip), **context) == [
        (clip, 60), (first, None), (cleanup, None)]
    assert sequence(clip, draws=(.99,), recent_clips=(first, cleanup, second, clip), **context) == [
        (clip, 60), (cleanup, None)]


@pytest.mark.parametrize('clip', ACTIVITY_ENDINGS)
def test_main_duration_stays_long_and_emotions_remain_a_minority(clip):
    actions = {clip: ACTIONS[clip], **ENDINGS}
    result_count = 0
    for seed in range(300):
        family, beats = choose_routine(random.Random(seed), actions, mode='rest')
        assert family.startswith('ambient:')
        assert beats[0][0] == clip and 45 <= beats[0][1] <= 90
        played = activity_sequence(random.Random(seed), actions, *beats[0])
        assert played[0] == beats[0]
        assert played[-1] == (ACTIVITY_ENDINGS[clip][-1], None)
        result_count += len(played) == 3
    assert 30 < result_count < 120


@pytest.mark.parametrize('clip', ACTIVITY_ENDINGS)
def test_state_interruption_discards_unplayed_tail_without_recording_it(clip):
    b = PetBrain(rng=random.Random(7), now=0)
    b.repertoire.actions = {clip: ACTIONS[clip], **ENDINGS}
    b.set_ground(0, 1440, 860)
    b.place(700)
    beats = sequence(clip)
    with patch.dict(CLIPS, clips(ENDINGS)):
        b._queue(*(Step(name, seconds) for name, seconds in beats), now=0, interrupt=True)
        b.set_state('think', 1, source='local')
        assert not any(step.clip in ENDINGS for step in (b.step, *b.plan) if step)
        assert not any(name in ENDINGS for name in b.repertoire.recent)
        assert b.state_name == 'think'
