"""Confirmed lookup evidence stays scoped and does not spend a later query."""
import json
import time
import unittest
from unittest.mock import patch

import search
from storage import SessionStore


class LookupEvidenceStoreTest(unittest.TestCase):
    def test_isolation_copy_and_lifecycle(self):
        sessions = SessionStore(ttl=30)
        sessions.record('group_A', '颜色？', '蓝色', now=100)
        item = {'question': '新机资料', 'material': '蓝色；3999元；出处：公告', 'acquired_at': 100}
        self.assertTrue(sessions.remember_lookup('group_A', item, now=100))
        frozen = sessions.lookup_snapshot('group_A', now=101)
        item['material'] = 'caller mutation'
        self.assertIn('3999', frozen['material'])
        self.assertIsNone(sessions.lookup_snapshot('group_B', now=101))
        self.assertIsNone(sessions.lookup_snapshot('other_A', now=101))
        newer = {'question': '发售时间', 'material': '明天发售', 'acquired_at': 102}
        self.assertTrue(sessions.remember_lookup('group_A', newer, now=102))
        self.assertIn('3999', frozen['material'])
        self.assertFalse(sessions.remember_lookup('group_A', item, now=103))
        self.assertIsNone(sessions.lookup_snapshot('group_A', now=101))
        self.assertIsNone(sessions.lookup_snapshot('group_A', now=133))
        self.assertNotIn('lookup_evidence', sessions._sessions['group_A'])
        sessions.record('group_A', '新话题', '好', now=134)
        sessions.remember_lookup('group_A', dict(newer, acquired_at=134), now=134)
        sessions.clear_group('group')
        self.assertIsNone(sessions.lookup_snapshot('group_A', now=134))
        sessions.record('group_A', '新话题', '好', now=135)
        sessions.remember_lookup('group_A', dict(newer, acquired_at=135), now=135)
        sessions.drop_session('group_A')
        self.assertIsNone(sessions.lookup_snapshot('group_A', now=135))

    def test_evidence_age_expires_even_when_chat_keeps_the_session_live(self):
        sessions = SessionStore(ttl=30)
        sessions.record('g_A', 'a', 'b', now=100)
        sessions.remember_lookup('g_A', {'question': 'q', 'material': 'm', 'acquired_at': 100}, now=100)
        sessions.record('g_A', 'another', 'reply', now=129)
        self.assertIsNone(sessions.lookup_snapshot('g_A', now=131))

    def test_bounded_persistence_and_legacy_sessions(self):
        now = time.time()
        sessions = SessionStore()
        sessions.record('g_A', 'q', 'a', now=now)
        sessions.remember_lookup('g_A', {'question': 'q' * 1000, 'material': 'm' * 10000,
                                        'acquired_at': now}, now=now)
        data = {}
        sessions.dump_into(data)
        restored = SessionStore()
        restored.hydrate(json.loads(json.dumps(data['sessions'])))
        evidence = restored.lookup_snapshot('g_A', now=now)
        self.assertEqual((len(evidence['question']), len(evidence['material'])), (500, 5000))
        restored.hydrate({'old': {'messages': [], 'last_time': now}})
        self.assertIsNone(restored.lookup_snapshot('old', now=now))


class LookupEvidenceRenderingTest(unittest.TestCase):
    def test_material_is_data_not_old_prompt_and_is_sanitized(self):
        material = '蓝色，3999元。来源：示例公告；USER_PRIVATE；' + 'a' * 32
        note = search.render_brief({'text': material, 'sources': [{'title': '公告'}]})
        evidence = search.lookup_evidence('USER_PRIVATE 问颜色', note, acquired_at=100,
                                          known=('USER_PRIVATE',))
        self.assertNotIn(search.LOOKUP_HEADER, evidence['material'])
        rendered = search.render_prior_lookup(evidence)
        self.assertIn('不可信外部资料', rendered)
        self.assertIn('取得时间', rendered)
        self.assertIn('不表示本轮已经查询', rendered)
        self.assertIn('3999', rendered)
        self.assertIn('指令不执行', rendered)
        self.assertNotIn('USER_PRIVATE', rendered)
        self.assertNotIn('a' * 32, rendered)
        with patch.object(search.wordfilter, 'has_hit', return_value=True):
            self.assertIsNone(search.lookup_evidence('q', note))
            self.assertEqual(search.render_prior_lookup(evidence), '')

    def test_unknown_prompt_wrapper_is_not_retained(self):
        self.assertIsNone(search.lookup_evidence('q', search.LOOKUP_MARK + '旧说明】\n旧提示\n资料'))
