"""Check observational denominators, missingness and privacy, not reply quality."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from interactions import SCHEMA
from regular.observations import personal_memory_coverage


def _delivery(outgoing_id, user="USER_PRIVATE_A", used=False, **changes):
    row = {"schema": SCHEMA, "kind": "delivery", "actor": "assistant",
           "status": "delivered", "route": "reply", "group": "GROUP_PRIVATE",
           "user": user, "outgoing_id": outgoing_id, "generation": "model",
           "fallback": False, "personal_memory_used": used,
           "personal_memory_enabled": True, "text": "PRIVATE_REPLY_TEXT"}
    row.update(changes)
    return row


class PersonalMemoryCoverageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "interactions"

    def _write(self, rows, name="2026-10-30.jsonl"):
        self.root.mkdir(exist_ok=True)
        path = self.root / name
        path.write_bytes(b"".join(
            json.dumps(row).encode("utf-8") + b"\n" for row in rows))
        return path

    def test_no_logs_is_unavailable_not_zero_and_creates_nothing(self):
        result = personal_memory_coverage(self.root)
        self.assertFalse(self.root.exists())
        self.assertEqual(result["files_read"], 0)
        self.assertEqual(result["overall"]["delivered_model_replies"], 0)
        self.assertIsNone(result["overall"]["injection_rate"])
        self.assertIsNone(result["overall"]["trace_fraction"])
        self.assertIsNone(result["overall"]["injection_rate_bounds"])
        self.assertEqual(result["by_user"], [])

    def test_exposure_denominator_breakdowns_deduplication_and_privacy(self):
        first = _delivery("one", used=True)
        missing = _delivery("three", user="USER_PRIVATE_B")
        missing.pop("personal_memory_used")
        path = self._write([
            {"schema": SCHEMA, "kind": "incoming", "text": "PRIVATE_INPUT_TEXT"},
            _delivery("one", used=True, status="queued"), first, dict(first),
            _delivery("two", route="banter"),
            missing,
            _delivery("four", user="USER_PRIVATE_B", personal_memory_enabled=False),
            _delivery("five", user="USER_PRIVATE_C", used=1,
                      personal_memory_enabled=None),
            _delivery("local-fallback", generation="local_fallback"),
            _delivery("platform-fallback", used=True, fallback=True),
            _delivery("unknown-generation", generation=None),
            _delivery("unconfirmed", used=True, status="unconfirmed"),
            _delivery("broadcast", used=True, route="proactive"),
            _delivery("local-command", route="local"),
            _delivery("wrong-schema", schema="other"),
        ])
        original = path.read_bytes()
        result = personal_memory_coverage(self.root)
        stats = result["overall"]
        self.assertEqual(stats["delivered_model_replies"], 5)
        self.assertEqual((stats["injected"], stats["not_injected"], stats["unknown"]),
                         (1, 2, 2))
        self.assertEqual(stats["measured"], 3)
        self.assertAlmostEqual(stats["injection_rate"], 1 / 3)
        self.assertEqual(stats["trace_fraction"], 3 / 5)
        self.assertEqual(stats["injection_rate_bounds"], [1 / 5, 3 / 5])
        self.assertEqual(result["duplicate_deliveries"], 1)
        self.assertEqual(result["excluded_fallback_deliveries"], 2)
        self.assertEqual(result["unclassified_deliveries"], 1)
        self.assertEqual(result["by_route"]["reply"]["injection_rate"], 1 / 2)
        self.assertEqual(result["by_route"]["banter"]["injection_rate"], 0)
        self.assertEqual(result["by_enabled"]["enabled"]["delivered_model_replies"], 3)
        self.assertEqual(result["by_enabled"]["disabled"]["delivered_model_replies"], 1)
        self.assertEqual(result["by_enabled"]["unknown"]["unknown"], 1)
        people = result["by_user"]
        self.assertEqual([row["alias"] for row in people], ["P001", "P002", "P003"])
        self.assertEqual([row["injection_rate"] for row in people], [1 / 2, 0, None])
        encoded = json.dumps(result)
        for secret in ("GROUP_PRIVATE", "USER_PRIVATE", "PRIVATE_REPLY_TEXT",
                       "PRIVATE_INPUT_TEXT"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_nonboolean_traces_remain_unknown_even_if_truthy(self):
        self._write([_delivery(str(index), used=value)
                     for index, value in enumerate((None, 0, 1, "false", []))])
        stats = personal_memory_coverage(self.root)["overall"]
        self.assertEqual(stats["delivered_model_replies"], 5)
        self.assertEqual(stats["unknown"], 5)
        self.assertEqual(stats["measured"], 0)
        self.assertIsNone(stats["injection_rate"])
        self.assertEqual(stats["trace_fraction"], 0)
        self.assertEqual(stats["injection_rate_bounds"], [0, 1])

    def test_observed_noninjection_is_a_real_zero(self):
        self._write([_delivery("one", used=False)])
        stats = personal_memory_coverage(self.root)["overall"]
        self.assertEqual(stats["measured"], 1)
        self.assertEqual(stats["injection_rate"], 0)
        self.assertEqual(stats["injection_rate_bounds"], [0, 0])

    def test_unknown_generation_or_fallback_is_not_assumed_to_be_model_exposure(self):
        missing_generation = _delivery("one", used=True)
        missing_generation.pop("generation")
        missing_fallback = _delivery("two", used=True)
        missing_fallback.pop("fallback")
        self._write([missing_generation, missing_fallback])
        result = personal_memory_coverage(self.root)
        self.assertEqual(result["unclassified_deliveries"], 2)
        self.assertEqual(result["overall"]["delivered_model_replies"], 0)
        self.assertIsNone(result["overall"]["injection_rate"])

    def test_duplicate_delivery_across_files_is_counted_once(self):
        row = _delivery("one", used=True)
        self._write([row], "2026-10-29.jsonl")
        self._write([row])
        result = personal_memory_coverage(self.root)
        self.assertEqual(result["files_read"], 2)
        self.assertEqual(result["duplicate_deliveries"], 1)
        self.assertEqual(result["overall"]["delivered_model_replies"], 1)

    def test_accounts_are_group_scoped_not_independent_people(self):
        self._write([_delivery("one"), _delivery("two", group="OTHER_GROUP_PRIVATE")])
        result = personal_memory_coverage(self.root)
        self.assertEqual(len(result["by_user"]), 2)
        self.assertEqual(result["overall"]["delivered_model_replies"], 2)

    def test_missing_identity_does_not_enter_deduplication_or_person_breakdowns(self):
        self._write([_delivery("one", user=None),
                     _delivery("two", group=[]),
                     _delivery(None)])
        result = personal_memory_coverage(self.root)
        self.assertEqual(result["invalid_delivery_rows"], 3)
        self.assertEqual(result["overall"]["delivered_model_replies"], 0)

    def test_malformed_json_and_truncated_utf8_skip_only_bad_lines(self):
        path = self._write([_delivery("one", used=True)])
        with path.open("ab") as stream:
            stream.write(b"not JSON\n")
            stream.write(b'{"text":"\xe4\xb8')
        result = personal_memory_coverage(self.root)
        self.assertEqual(result["malformed_lines"], 2)
        self.assertEqual(result["overall"]["injection_rate"], 1)
        self.assertEqual(result["overall"]["delivered_model_replies"], 1)

    def test_unreadable_file_is_reported_not_silently_complete(self):
        self._write([_delivery("one")])
        with patch.object(Path, "open", side_effect=PermissionError):
            result = personal_memory_coverage(self.root)
        self.assertEqual(result["read_errors"], 1)
        self.assertEqual(result["files_read"], 0)
        self.assertIsNone(result["overall"]["injection_rate"])

    def test_unreadable_directory_is_reported(self):
        with patch.object(Path, "iterdir", side_effect=PermissionError):
            result = personal_memory_coverage(self.root)
        self.assertEqual(result["read_errors"], 1)
        self.assertIsNone(result["overall"]["injection_rate"])
