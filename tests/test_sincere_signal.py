"""A usable sincere judgment remains a dynamic cue, without changing gate or truth rules."""
import unittest
from unittest.mock import AsyncMock, patch

import bot
import prompts
from regular import decisions
from test_natural_feedback import _Base
from test_triage import body_for


class SincereRoutingTest(_Base):
    async def test_real_triage_reaches_actual_dynamic_layer(self):
        self.calls.side_effect = lambda text, qs: body_for(qs, request_kind='sincere', next_move='continue_normally', reminder_request='none')
        await self._drive('这个公告是哪天发的', is_at=True)
        self.calls.assert_awaited_once()
        messages = self.model_calls[-1]
        self.assertIn(prompts.PROMPT_SINCERE_REQUEST, messages[-1]['content'])
        self.assertNotIn(prompts.PROMPT_SINCERE_REQUEST, '\n'.join(m['content'] for m in messages[:-1]))

    async def test_sincere_cue_is_exclusive_and_feedback_has_priority(self):
        await bot.get_ai_reply('sincere-exclusive', '具体是哪天', sincere_request=True,
                               playful_request=True, playful_note='旧版玩笑线索')
        tail = self.model_calls[-1][-1]['content']
        self.assertIn(prompts.PROMPT_SINCERE_REQUEST, tail)
        self.assertNotIn(prompts.PROMPT_PLAYFUL_REQUEST, tail)
        self.assertNotIn('旧版玩笑线索', tail)
        for priority in ({'back_off': True}, {'feedback_note': prompts.PROMPT_FEEDBACK_REPAIR}):
            await bot.get_ai_reply('sincere-priority', '具体是哪天', sincere_request=True, **priority)
            self.assertNotIn(prompts.PROMPT_SINCERE_REQUEST, self.model_calls[-1][-1]['content'])


class SincereDecisionTest(unittest.IsolatedAsyncioTestCase):
    async def test_sincere_and_playful_are_mutually_exclusive(self):
        for choice in ('sincere', 'playful', 'not_request'):
            tracker = decisions.FeedbackTracker(120, 180)
            call = AsyncMock(side_effect=lambda text, qs: body_for(qs, request_kind=choice))
            with patch.object(decisions.providers, 'call_decision', call):
                result = await tracker.assess('g', 'u', '具体时间呢', is_direct=True, now=10)
            self.assertEqual(bool(result.get('sincere_request')), choice == 'sincere')
            self.assertEqual(bool(result.get('playful_request')), choice == 'playful')
            call.assert_awaited_once()

    async def test_abstained_and_refused_judgments_do_not_produce_sincere(self):
        for mode in ('refusal', 'low_margin'):
            def response(text, qs):
                result = body_for(qs, request_kind='sincere')
                row = next(r for r in result['answers'] if r['name'] == 'request_kind')
                if mode == 'refusal':
                    row.clear(); row.update(name='request_kind', type='refusal')
                else:
                    row['probabilities'] = [{'value': k, 'probability': p}
                                           for k, p in (('sincere', .4), ('playful', .35), ('not_request', .25))]
                return result
            tracker = decisions.FeedbackTracker(120, 180)
            with patch.object(decisions.providers, 'call_decision', AsyncMock(side_effect=response)):
                result = await tracker.assess('g', 'u', '时间呢', is_direct=True, now=10)
            self.assertNotIn('sincere_request', result)

    async def test_stale_result_does_not_produce_sincere(self):
        tracker = decisions.FeedbackTracker(120, 180)
        tracker.remember('g', 'u', '旧回复', outgoing_id='old', route='reply', now=0)
        def response(text, qs):
            tracker.remember('g', 'u', '新回复', outgoing_id='new', route='reply', now=6)
            return body_for(qs, request_kind='sincere')
        with patch.object(decisions.providers, 'call_decision', AsyncMock(side_effect=response)):
            result = await tracker.assess('g', 'u', '旧回复的时间', is_direct=True, now=5)
        self.assertEqual(result['action'], 'stay_quiet')
        self.assertNotIn('sincere_request', result)
