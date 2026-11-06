import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.settings import load_settings
from app.service.query import experiment_config_hash


class ExperimentHashTests(unittest.TestCase):
    def test_prompt_change_changes_config_hash(self):
        settings = load_settings()
        with tempfile.TemporaryDirectory() as folder:
            prompt = Path(folder) / "prompt.txt"
            with patch("app.service.query.DEFAULT_PROMPT", prompt):
                prompt.write_text("첫 프롬프트", encoding="utf-8")
                before = experiment_config_hash(settings)
                prompt.write_text("바뀐 프롬프트", encoding="utf-8")
                after = experiment_config_hash(settings)
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
