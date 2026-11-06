"""Read knowledge entries, preview changes, preserve the original Markdown."""
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import panel
import test_panel_onboarding as onboarding


class Forms(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.forms, self.current, self.textarea = [], None, None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action"), "fields": {}}
            self.forms.append(self.current)
        elif self.current is not None and tag == "input" and attrs.get("name"):
            self.current["fields"][attrs["name"]] = attrs.get("value", "")
        elif self.current is not None and tag == "textarea" and attrs.get("name"):
            self.textarea = attrs["name"]
            self.current["fields"][self.textarea] = ""

    def handle_data(self, data):
        if self.current is not None and self.textarea:
            self.current["fields"][self.textarea] += data

    def handle_endtag(self, tag):
        if tag == "form": self.current = None
        elif tag == "textarea": self.textarea = None


class KnowledgeEntryTest(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = onboarding.PanelOnboardingTest.asyncTearDown
    auth = onboarding.PanelOnboardingTest.auth

    async def asyncSetUp(self):
        await onboarding.PanelOnboardingTest.asyncSetUp(self)
        self.directory = Path(self.cfg.KNOWLEDGE_DIR)
        self.directory.mkdir(parents=True)
        self.path = self.directory / "all.md"

    async def page(self, query=""):
        response = await self.client.get("/knowledge" + query, headers=self.auth())
        self.assertEqual(response.status, 200)
        return await response.text()

    async def editor(self, query="?new=1"):
        text = await self.page(query)
        return next(f["fields"] for f in Forms(text).forms if f["action"] == "/knowledge/entry/preview")

    async def preview(self, fields):
        response = await self.client.post("/knowledge/entry/preview", headers=self.auth(), data=fields)
        self.assertEqual(response.status, 200)
        text = await response.text()
        return text, next(f["fields"] for f in Forms(text).forms if f["action"] == "/knowledge/entry/save")

    async def save(self, fields):
        return await self.client.post("/knowledge/entry/save", headers=self.auth(), data=fields, allow_redirects=False)

    async def test_default_view_is_a_list_and_full_source_stays_folded(self):
        self.path.write_text("## 奶茶局\n关键词：奶茶、报名\n周五在公告下报名。\n")
        text = await self.page()
        self.assertIn("奶茶局", text)
        self.assertIn("新增知识", text)
        self.assertNotIn("data-kb-editor", text)
        self.assertNotIn("knowledge-composer", text)
        self.assertNotIn("群号.md", text)
        self.assertIn('<details class="kb-source">', text)
        self.runner.start.assert_not_called()

    async def test_add_requires_preview_then_confirmation(self):
        fields = await self.editor()
        fields.update(title="周五奶茶局", keywords="奶茶、报名", body="周五在公告下报名。")
        text, confirmation = await self.preview(fields)
        self.assertIn("尚未保存", text)
        self.assertFalse(self.path.exists())
        self.assertEqual((await self.save(confirmation)).status, 302)
        self.assertEqual(self.path.read_text(), "## 周五奶茶局\n关键词：奶茶、报名\n周五在公告下报名。\n")
        self.runner.start.assert_not_called()
        self.runner.stop.assert_not_awaited()

    async def test_edit_preserves_preamble_comments_and_unparsed_other_sections(self):
        prefix = "# 手册\n暂未整理的原稿。\n<!-- ## 注释里的假条目\n继续保留 -->\n\n"
        suffix = "## 空条目\n关键词：空条目\n\n## 第二条\n关键词：测试\n第二条原文。\n"
        self.path.write_text(prefix + "## 第一条\n关键词：例子\n第一条原文。\n\n" + suffix)
        fields = await self.editor("?edit=0")
        self.assertEqual(fields["title"], "第一条")
        fields["body"] = "修改后的第一条。"
        _, confirmation = await self.preview(fields)
        self.assertEqual((await self.save(confirmation)).status, 302)
        changed = self.path.read_text()
        self.assertTrue(changed.startswith(prefix))
        self.assertTrue(changed.endswith(suffix))
        self.assertIn("修改后的第一条", changed)
        text = await self.page()
        self.assertIn("还有一段未整理的内容", text)
        self.assertIn("空条目", text)
        self.assertIn("待整理", text)

    async def test_delete_only_writes_after_confirmation_and_removes_one_span(self):
        prefix = "保留这段说明。\n\n"
        suffix = "## 第二条\n关键词：测试\n第二条保持原样。\n"
        original = prefix + "## 第一条\n关键词：例子\n要删除的内容。\n\n" + suffix
        self.path.write_text(original)
        text = await self.page("?delete=0")
        self.assertIn("删除这条知识？", text)
        self.assertEqual(self.path.read_text(), original)
        fields = next(f["fields"] for f in Forms(text).forms if f["action"] == "/knowledge/entry/save")
        self.assertEqual((await self.save(fields)).status, 302)
        self.assertEqual(self.path.read_text(), prefix + suffix)

    async def test_newer_content_is_not_overwritten_by_a_stale_confirmation(self):
        fields = await self.editor()
        fields.update(title="奶茶局", keywords="奶茶", body="草稿。")
        _, confirmation = await self.preview(fields)
        newer = "## 新消息\n后来补的内容。\n"
        self.path.write_text(newer)
        response = await self.save(confirmation)
        self.assertEqual(response.status, 409)
        self.assertIn("内容已有更新，请重新预览", await response.text())
        self.assertEqual(self.path.read_text(), newer)

    async def test_scope_labels_are_stable_and_group_save_uses_the_existing_format(self):
        state = Path(self.cfg.STATE_FILE)
        state.write_text(json.dumps({"groups": {"group-beta": {"name": "奶茶群"}}}))
        self.assertEqual(dict(panel._knowledge_scopes(self.cfg, "all.md"))["group-beta.md"], "奶茶群")
        state.write_text(json.dumps({"groups": {"group-beta": {}}}))
        label = dict(panel._knowledge_scopes(self.cfg, "all.md"))["group-beta.md"]
        state.write_text(json.dumps({"groups": {"group-alpha": {}, "group-beta": {}}}))
        self.assertEqual(dict(panel._knowledge_scopes(self.cfg, "all.md"))["group-beta.md"], label)
        fields = await self.editor("?file=group-beta.md&new=1")
        fields.update(title="本群约定", body="周末再聚。")
        _, confirmation = await self.preview(fields)
        self.assertEqual((await self.save(confirmation)).status, 302)
        self.assertTrue((self.directory / "group-beta.md").is_file())
        self.assertFalse(self.path.exists())

    async def test_auth_csrf_methods_validation_and_sensitive_filter(self):
        fields = await self.editor()
        fields.update(title="正常标题", body="正常内容。")
        for route in ("/knowledge/entry/preview", "/knowledge/entry/save"):
            self.assertEqual((await self.client.post(route, data=fields)).status, 401)
            self.assertEqual((await self.client.post(route, headers=self.auth(), data={**fields, "csrf": "bad"})).status, 400)
            self.assertEqual((await self.client.get(route, headers=self.auth())).status, 405)
        for change in ({"file": "../escape.md"}, {"title": "一行\n另一行"},
                       {"body": "正文\n## 偷偷增加另一条\n内容"}, {"action": "unknown"}):
            response = await self.client.post('/knowledge/entry/preview', headers=self.auth(), data={**fields, **change})
            self.assertEqual(response.status, 400)
        with patch.object(panel.knowledge_mod.wordfilter, 'has_hit', return_value=True):
            response = await self.client.post('/knowledge/entry/preview', headers=self.auth(), data=fields)
            self.assertEqual(response.status, 400)
        with patch.object(panel.knowledge_mod.wordfilter, 'has_hit', side_effect=lambda text: '敏感关联词' in text):
            response = await self.client.post('/knowledge/entry/preview', headers=self.auth(),
                                              data={**fields, 'keywords': '敏感关联词'})
            self.assertEqual(response.status, 400)
        self.assertFalse(self.path.exists())

    async def test_preview_escapes_markup_and_return_to_edit_keeps_the_draft(self):
        fields = await self.editor()
        fields.update(title="引用示例", body='<img src=x onerror="alert(1)">只是文字')
        text, confirmation = await self.preview(fields)
        self.assertNotIn('<img src=x', text)
        self.assertIn('&lt;img src=x', text)
        response = await self.client.post('/knowledge/entry/preview', headers=self.auth(), data={**confirmation, 'intent': 'edit'})
        self.assertEqual(response.status, 200)
        text = await response.text()
        self.assertIn('data-kb-editor', text)
        self.assertIn('&lt;img src=x', text)
        self.assertFalse(self.path.exists())

    async def test_template_is_an_unsaved_new_entry_not_a_replacement(self):
        original = "## 已有约定\n关键词：已有\n保持原样。\n"
        self.path.write_text(original)
        text = await self.page('?template=names')
        self.assertIn('示例草稿', text)
        self.assertIn('一个称呼的来历', text)
        self.assertEqual(self.path.read_text(), original)

    async def test_an_existing_title_is_not_silently_added_as_an_unused_duplicate(self):
        original = "## 奶茶局\n关键词：奶茶\n已经保存的约定。\n"
        self.path.write_text(original)
        fields = await self.editor()
        fields.update(title="奶茶局", body="另一份约定。")
        response = await self.client.post('/knowledge/entry/preview', headers=self.auth(), data=fields)
        self.assertEqual(response.status, 400)
        self.assertIn('已经有同名知识', await response.text())
        self.assertEqual(self.path.read_text(), original)


if __name__ == '__main__':
    unittest.main()
