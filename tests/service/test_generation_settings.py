"""생성 모델 설정: Advanced 추론 강도·출력 한도와 모델 생성 인자."""

from contextlib import ExitStack
import os
import unittest
from unittest.mock import patch

from app.service.resources import _create_generation_model
from shared.settings import PROJECT_ROOT, load_settings


class GenerationSettingsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}))
        self.enterContext(patch("app.service.resources.OpenAI"))
        self.chat = self.enterContext(patch("app.service.resources.ChatOpenAI"))

    def test_advanced_uses_low_reasoning_and_4096_output_tokens(self):
        settings = load_settings(PROJECT_ROOT / "configs" / "advanced.yaml")
        self.assertEqual(settings.reasoning_effort, "low")
        self.assertEqual(settings.max_output_tokens, 4096)
        with ExitStack() as cleanup:
            _create_generation_model(settings, cleanup)
        kwargs = self.chat.call_args.kwargs
        self.assertEqual(kwargs["reasoning_effort"], "low")
        self.assertEqual(kwargs["max_tokens"], 4096)

    def test_unset_reasoning_effort_keeps_model_default(self):
        settings = load_settings(PROJECT_ROOT / "configs" / "basic.yaml")
        self.assertIsNone(settings.reasoning_effort)
        with ExitStack() as cleanup:
            _create_generation_model(settings, cleanup)
        self.assertNotIn("reasoning_effort", self.chat.call_args.kwargs)

    def test_unknown_reasoning_effort_is_rejected(self):
        settings = load_settings(PROJECT_ROOT / "configs" / "advanced.yaml")
        with self.assertRaises(ValueError):
            type(settings).model_validate({**settings.model_dump(), "reasoning_effort": "fast"})


if __name__ == "__main__":
    unittest.main()
