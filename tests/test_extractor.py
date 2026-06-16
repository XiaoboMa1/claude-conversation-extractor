"""Tests for Claude Conversation Extractor"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from extract_claude_logs import ClaudeConversationExtractor  # noqa: E402


class TestClaudeConversationExtractor(unittest.TestCase):
    """Test suite for the Claude Conversation Extractor"""

    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.extractor = ClaudeConversationExtractor(output_dir=self.temp_dir)

    def tearDown(self):
        """Clean up test fixtures"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initialization(self):
        """Test extractor initialization"""
        self.assertEqual(self.extractor.output_dir, Path(self.temp_dir))
        self.assertTrue(self.extractor.claude_dir.name == "projects")

    def test_extract_text_content_string(self):
        """Test extracting text from string content"""
        content = "Hello, world!"
        result = self.extractor._extract_text_content(content)
        self.assertEqual(result, "Hello, world!")

    def test_extract_text_content_list(self):
        """Test extracting text from list content"""
        content = [
            {"type": "text", "text": "First part"},
            {"type": "text", "text": "Second part"},
            {"type": "other", "text": "Should ignore"},
        ]
        result = self.extractor._extract_text_content(content)
        self.assertEqual(result, "First part\nSecond part")

    def test_extract_text_content_other(self):
        """Test extracting text from other content types"""
        content = {"some": "dict"}
        result = self.extractor._extract_text_content(content)
        self.assertEqual(result, "{'some': 'dict'}")

    def test_save_as_markdown_empty_conversation(self):
        """Test saving empty conversation returns None"""
        result = self.extractor.save_as_markdown([], "test-session")
        self.assertIsNone(result)

    def test_save_as_markdown_with_conversation(self):
        """Test saving conversation to markdown"""
        conversation = [
            {
                "role": "user",
                "content": "Hello Claude",
                "timestamp": "2025-05-25T10:00:00Z",
            },
            {
                "role": "assistant",
                "content": "Hello! How can I help?",
                "timestamp": "2025-05-25T10:00:01Z",
            },
        ]

        result = self.extractor.save_as_markdown(conversation, "test-session-id")

        self.assertIsNotNone(result)
        self.assertTrue(result.exists())
        self.assertTrue(result.name.startswith("claude-chat-"))
        self.assertTrue(result.name.endswith(".md"))

        # Check content
        content = result.read_text()
        self.assertIn("# Claude Conversation Log", content)
        self.assertIn("Session ID: test-session-id", content)
        self.assertIn("## User", content)
        self.assertIn("Hello Claude", content)
        self.assertIn("## Claude", content)
        self.assertIn("Hello! How can I help?", content)

    def test_extract_conversation_valid_jsonl(self):
        """Test extracting conversation from valid JSONL"""
        # Create a temporary JSONL file
        jsonl_file = Path(self.temp_dir) / "test.jsonl"

        entries = [
            {
                "type": "user",
                "message": {"role": "user", "content": "Test message"},
                "timestamp": "2025-05-25T10:00:00Z",
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Test response"}],
                },
                "timestamp": "2025-05-25T10:00:01Z",
            },
        ]

        with open(jsonl_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        conversation = self.extractor.extract_conversation(jsonl_file)

        self.assertEqual(len(conversation), 2)
        self.assertEqual(conversation[0]["role"], "user")
        self.assertEqual(conversation[0]["content"], "Test message")
        self.assertEqual(conversation[1]["role"], "assistant")
        self.assertEqual(conversation[1]["content"], "Test response")

    def test_extract_conversation_invalid_file(self):
        """Test extracting conversation from non-existent file"""
        fake_path = Path(self.temp_dir) / "non_existent.jsonl"
        conversation = self.extractor.extract_conversation(fake_path)
        self.assertEqual(conversation, [])

    @patch("extractor.Path.rglob")
    def test_find_sessions(self, mock_rglob):
        """Test finding session files"""
        # Mock some session files
        mock_files = [
            MagicMock(stat=MagicMock(return_value=MagicMock(st_mtime=1000))),
            MagicMock(stat=MagicMock(return_value=MagicMock(st_mtime=2000))),
            MagicMock(stat=MagicMock(return_value=MagicMock(st_mtime=1500))),
        ]
        mock_rglob.return_value = mock_files

        sessions = self.extractor.find_sessions()

        # Should be sorted by modification time, newest first
        self.assertEqual(len(sessions), 3)
        self.assertEqual(sessions[0].stat().st_mtime, 2000)
        self.assertEqual(sessions[1].stat().st_mtime, 1500)
        self.assertEqual(sessions[2].stat().st_mtime, 1000)


    def test_merge_consecutive_same_role(self):
        """Test that consecutive messages from the same speaker are merged."""
        conversation = [
            {"role": "user", "content": "First message", "timestamp": ""},
            {"role": "user", "content": "Second message", "timestamp": ""},
            {"role": "assistant", "content": "Reply one", "timestamp": ""},
            {"role": "assistant", "content": "Reply two", "timestamp": ""},
            {"role": "user", "content": "Third message", "timestamp": ""},
        ]
        result = self.extractor._merge_and_clean(conversation)
        self.assertEqual(len(result), 3)
        self.assertIn("First message", result[0]["content"])
        self.assertIn("Second message", result[0]["content"])
        self.assertIn("Reply one", result[1]["content"])
        self.assertIn("Reply two", result[1]["content"])
        self.assertEqual(result[2]["content"], "Third message")

    def test_filter_noise_tags(self):
        """Test that XML noise tags are stripped from content."""
        conversation = [
            {
                "role": "user",
                "content": '<local-command-caveat>noise</local-command-caveat>\nReal content',
                "timestamp": "",
            },
        ]
        result = self.extractor._merge_and_clean(conversation)
        self.assertEqual(len(result), 1)
        self.assertNotIn("local-command-caveat", result[0]["content"])
        self.assertIn("Real content", result[0]["content"])

    def test_filter_noise_only_messages(self):
        """Test that pure-noise messages are dropped entirely."""
        conversation = [
            {"role": "user", "content": "Real question", "timestamp": ""},
            {
                "role": "assistant",
                "content": "You've hit your limit resets 1:20pm",
                "timestamp": "",
            },
            {"role": "assistant", "content": "Prompt is too long", "timestamp": ""},
            {"role": "assistant", "content": "Actual answer", "timestamp": ""},
        ]
        result = self.extractor._merge_and_clean(conversation)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["content"], "Real question")
        self.assertEqual(result[1]["content"], "Actual answer")

    def test_filter_command_tags_dropped(self):
        """Test that messages consisting only of command tags are dropped."""
        conversation = [
            {
                "role": "user",
                "content": "<command-name>/effort</command-name>\n<command-message>effort</command-message>\n<command-args>max</command-args>",
                "timestamp": "",
            },
            {"role": "user", "content": "Actual question", "timestamp": ""},
        ]
        result = self.extractor._merge_and_clean(conversation)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "Actual question")


if __name__ == "__main__":
    unittest.main()
