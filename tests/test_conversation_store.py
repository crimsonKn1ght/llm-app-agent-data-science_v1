from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from app.memory import conversation_store


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir = conversation_store.CONVERSATIONS_DIR
        self.old_locks = conversation_store._conversation_locks
        conversation_store.CONVERSATIONS_DIR = Path(self.tmp.name)
        conversation_store._conversation_locks = {}

    def tearDown(self):
        conversation_store.CONVERSATIONS_DIR = self.old_dir
        conversation_store._conversation_locks = self.old_locks
        self.tmp.cleanup()

    def test_invalid_conversation_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            conversation_store.append_turn("not-a-uuid", "hello", "hi")

    def test_path_traversal_cannot_write_outside_directory(self):
        with self.assertRaises(ValueError):
            conversation_store.append_turn("../outside", "hello", "hi")

        self.assertFalse((Path(self.tmp.name).parent / "outside.json").exists())

    def test_append_writes_valid_json(self):
        conversation_id = str(uuid.uuid4())
        conversation_store.append_turn(conversation_id, "hello", "hi")

        path = conversation_store._conv_path(conversation_id)
        data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["summary"], "")
        self.assertEqual(len(data["history"]), 2)
        self.assertEqual(data["history"][0], {"role": "user", "content": "hello"})

    def test_corrupted_json_is_renamed_and_starts_clean(self):
        conversation_id = str(uuid.uuid4())
        path = conversation_store._conv_path(conversation_id)
        path.write_text("{bad json", encoding="utf-8")

        data = conversation_store.load(conversation_id)

        self.assertEqual(data, {"history": [], "summary": ""})
        self.assertFalse(path.exists())
        backups = list(Path(self.tmp.name).glob("*.corrupt-*.json"))
        self.assertEqual(len(backups), 1)

    def test_malformed_fields_normalize_to_safe_defaults(self):
        data = conversation_store.build_context({
            "history": [
                {"role": "user", "content": "valid"},
                {"role": "bad", "content": "skip"},
                {"role": "assistant", "content": 123},
            ],
            "summary": ["not a string"],
        })

        self.assertEqual(data, ([{"role": "user", "content": "valid"}], ""))

    def test_repeated_appends_preserve_all_turns(self):
        conversation_id = str(uuid.uuid4())
        conversation_store.append_turn(conversation_id, "u1", "a1")
        conversation_store.append_turn(conversation_id, "u2", "a2")

        data = conversation_store.load(conversation_id)

        self.assertIsNotNone(data)
        self.assertEqual(len(data["history"]), 4)
        self.assertEqual(data["history"][2], {"role": "user", "content": "u2"})


if __name__ == "__main__":
    unittest.main()
