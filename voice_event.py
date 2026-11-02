"""Extract only current-message QQ voice fields from the raw gateway event.

No data here is logged or persisted.  The caller must not log the attachment.
"""

from voice_input import VoiceAttachment


def _attachments(value):
    if isinstance(value, dict):
        return (value,)
    return value if isinstance(value, list) else ()


def _is_voice(item):
    if not isinstance(item, dict):
        return False
    kind = item.get("content_type")
    if not isinstance(kind, str):
        return False
    kind = kind.lower().split(";", 1)[0].strip()
    return kind.startswith("audio/") or kind in ("voice", "audio", "application/x-silk")


def extract_voice_attachments(data):
    """Return validated-shape fields for current-message voice attachments.

    QQ attachment fields match Tencent's qqbot-agent-sdk MessageAttachment.
    The actual URL/network and transcript bounds are enforced by VoiceProcessor.
    msg_elements in reference/history messages belong to other messages and are
    intentionally not transcribed as though the current sender spoke them.
    """
    if not isinstance(data, dict):
        return ()
    items = list(_attachments(data.get("attachments")))
    scene = data.get("message_scene")
    ext = scene.get("ext") if isinstance(scene, dict) else None
    ref_idx = ext.get("ref_msg_idx") if isinstance(ext, dict) else None
    if str(data.get("message_type", "")) not in ("102", "103"):
        elements = data.get("msg_elements")
        if isinstance(elements, list):
            for elem in elements:
                if not isinstance(elem, dict):
                    continue
                if any(str(elem.get(k, "")) in ("102", "103")
                       for k in ("type", "msg_type", "message_type")):
                    continue
                if ref_idx and elem.get("msg_idx") == ref_idx:
                    continue
                items.extend(_attachments(elem.get("attachments")))
    records = []
    for item in items:
        if not _is_voice(item):
            continue
        identifiers = {(name, item[name]) for name in ("id", "url")
                       if isinstance(item.get(name), str) and item[name]}
        def text(name):
            value = item.get(name)
            return value if isinstance(value, str) and value else None
        values = {"reference_text": text("asr_refer_text"),
                  "wav_url": text("voice_wav_url"), "audio_url": text("url")}
        matches = [r for r in records if identifiers and r["ids"] & identifiers]
        if not matches:
            records.append({"ids": identifiers, "values": values})
            continue
        first = matches[0]
        for duplicate in matches[1:]:
            first["ids"].update(duplicate["ids"])
            for key, value in duplicate["values"].items():
                if not first["values"][key] and value:
                    first["values"][key] = value
            records.remove(duplicate)
        first["ids"].update(identifiers)
        for key, value in values.items():
            if not first["values"][key] and value:
                first["values"][key] = value
    return tuple(VoiceAttachment(**r["values"]) for r in records)


def make_voice_message_class(base):
    """Add one hidden slot to an SDK message without retaining the raw event."""
    class VoiceMessage(base):
        __slots__ = ("_voice_attachments",)

        def __init__(self, api, event_id, data):
            super().__init__(api, event_id, data)
            self._voice_attachments = extract_voice_attachments(data)

        def __repr__(self):
            # Do not expose either URL or transcript from a voice event.
            if self._voice_attachments:
                return "<VoiceMessage>"
            return super().__repr__()
    return VoiceMessage


def make_voice_parser(message_class, dispatch_name):
    """Build an SDK ConnectionState parser with the original dispatch contract."""
    voice_class = make_voice_message_class(message_class)

    def parse(self, payload):
        message = voice_class(self.api, payload.get("id", None), payload.get("d", {}))
        self._dispatch(dispatch_name, message)
    return parse
