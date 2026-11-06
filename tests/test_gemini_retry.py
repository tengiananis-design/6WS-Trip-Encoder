import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.ai_analyzer import _generate_with_retry


class TransientGeminiError(Exception):
    status_code = 429


class GeminiRetryTests(unittest.TestCase):
    def test_retries_transient_rate_limit_then_succeeds(self):
        response = SimpleNamespace(text='{"ok": true}')
        models = SimpleNamespace(generate_content=unittest.mock.Mock(
            side_effect=[TransientGeminiError("rate limited"), response]
        ))
        client = SimpleNamespace(models=models)

        with patch("services.ai_analyzer.time.sleep") as sleep, patch(
            "services.ai_analyzer.types.GenerateContentConfig", return_value={}
        ):
            actual = _generate_with_retry(client, "test prompt")

        self.assertIs(actual, response)
        self.assertEqual(models.generate_content.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_does_not_retry_non_transient_errors(self):
        models = SimpleNamespace(generate_content=unittest.mock.Mock(
            side_effect=ValueError("invalid request")
        ))
        client = SimpleNamespace(models=models)

        with patch("services.ai_analyzer.time.sleep") as sleep, patch(
            "services.ai_analyzer.types.GenerateContentConfig", return_value={}
        ):
            with self.assertRaises(ValueError):
                _generate_with_retry(client, "test prompt")

        self.assertEqual(models.generate_content.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
