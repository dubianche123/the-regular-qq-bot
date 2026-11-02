import unittest

from voice_event import extract_voice_attachments, make_voice_parser


class Base:
    __slots__ = ("id", "content", "attachments", "event_id")
    def __init__(self, api, event_id, data):
        self.id = data.get("id")
        self.content = data.get("content")
        self.attachments = data.get("attachments", [])
        self.event_id = event_id


class Group(Base):
    __slots__ = ("author", "group_openid")
    def __init__(self, api, event_id, data):
        super().__init__(api, event_id, data)
        self.author = data.get("author")
        self.group_openid = data.get("group_openid")


class C2C(Base):
    __slots__ = ("author",)
    def __init__(self, api, event_id, data):
        super().__init__(api, event_id, data)
        self.author = data.get("author")


class State:
    api = object()
    def _dispatch(self, name, message):
        self.dispatched = name, message


def voice(**extra):
    return {"content_type": "voice", "filename": "voice.amr", "url": "https://example.invalid/secret", **extra}


class VoiceEventTest(unittest.TestCase):
    def test_fields_retained_for_real_shape(self):
        fields = extract_voice_attachments({"attachments": [voice(asr_refer_text="private text", voice_wav_url="https://example.invalid/wav")]})
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].reference_text, "private text")
        self.assertEqual(fields[0].wav_url, "https://example.invalid/wav")
        self.assertEqual(fields[0].audio_url, "https://example.invalid/secret")
        self.assertNotIn("private text", repr(fields))
        self.assertNotIn("example.invalid", repr(fields))

    def test_elements_and_dedup(self):
        attachment = voice(id="same", asr_refer_text="one")
        data = {"attachments": [attachment], "msg_elements": [{"attachments": [attachment, voice(id="two", url="https://example.invalid/2")]}]}
        self.assertEqual(len(extract_voice_attachments(data)), 2)

    def test_duplicates_merge_missing_fields_and_keep_first_values(self):
        first = voice(id="one")
        duplicate = voice(id="one", asr_refer_text="recognition", voice_wav_url="https://example.invalid/wav")
        values = extract_voice_attachments({"attachments": [first], "msg_elements": [{"attachments": [duplicate]}]})
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].reference_text, "recognition")
        self.assertEqual(values[0].wav_url, "https://example.invalid/wav")
        later = voice(id="one", asr_refer_text="different")
        values = extract_voice_attachments({"attachments": [duplicate, later]})
        self.assertEqual(values[0].reference_text, "recognition")

    def test_ref_or_history_not_treated_as_current_voice(self):
        for message_type in (102, 103, "103"):
            self.assertEqual(extract_voice_attachments({"message_type": message_type, "msg_elements": [{"attachments": [voice()]}]}), ())
        self.assertEqual(extract_voice_attachments({"message_scene": {"ext": {"ref_msg_idx": "q"}}, "msg_elements": [{"msg_idx": "q", "attachments": [voice()]}]}), ())
        self.assertEqual(extract_voice_attachments({"msg_elements": [{"type": 103, "attachments": [voice()]}]}), ())
        # A current top-level attachment still counts even on a reply.
        self.assertEqual(len(extract_voice_attachments({"message_type": 103, "attachments": [voice()], "msg_elements": [{"attachments": [voice(url="other")]}]})), 1)

    def test_ignores_nonvoice_and_unknown_fields(self):
        self.assertEqual(extract_voice_attachments({"attachments": [{"content_type": "image/jpeg", "asr_refer_text": "no"}, {"url": "something", "voice": True}]}), ())
        self.assertEqual(extract_voice_attachments(None), ())
        value = extract_voice_attachments({"attachments": [voice(asr_refer_text=7, voice_wav_url={})]})[0]
        self.assertIsNone(value.reference_text)
        self.assertIsNone(value.wav_url)

    def test_all_three_dispatch_contracts_and_redacted_repr(self):
        for cls, event in ((Group, "group_message_create"), (Group, "group_at_message_create"), (C2C, "c2c_message_create")):
            state = State()
            parser = make_voice_parser(cls, event)
            parser(state, {"id": "gateway-id", "d": {"id": "message-id", "content": "hi", "author": "alice", "group_openid": "group", "attachments": [voice(asr_refer_text="private transcript")]}})
            name, message = state.dispatched
            self.assertEqual(name, event)
            self.assertIsInstance(message, cls)
            self.assertEqual(message.event_id, "gateway-id")
            self.assertEqual(message.id, "message-id")
            self.assertEqual(message.content, "hi")
            self.assertEqual(message._voice_attachments[0].reference_text, "private transcript")
            self.assertNotIn("private transcript", repr(message))
            self.assertNotIn("example.invalid", repr(message))

    def test_actual_botpy_slot_classes(self):
        from botpy.message import GroupMessage, C2CMessage
        for cls, event, author in ((GroupMessage, "group_message_create", {"member_openid": "member"}),
                                   (GroupMessage, "group_at_message_create", {"member_openid": "member"}),
                                   (C2CMessage, "c2c_message_create", {"user_openid": "user"})):
            state = State()
            make_voice_parser(cls, event)(state, {"id": "gateway", "d": {"id": "message", "author": author, "content": "hello", "attachments": [voice(asr_refer_text="private")]}})
            name, message = state.dispatched
            self.assertEqual(name, event)
            self.assertEqual(message._voice_attachments[0].reference_text, "private")
            self.assertNotIn("example.invalid", repr(message))
            self.assertNotIn("private", repr(message))


if __name__ == "__main__":
    unittest.main()
