import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from openai.types.chat import ChatCompletion

import demo


def completion(
    content: str | None = "Paris is the capital of France.",
    refusal: str | None = None,
) -> ChatCompletion:
    return ChatCompletion(
        id="test",
        created=0,
        model="gpt-4.1-mini-2025-04-14",
        object="chat.completion",
        choices=[
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
        usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    )


class DemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict("os.environ", {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_default_prompts_use_one_router_and_report_serving_model(self) -> None:
        output, requests = self.run_demo([])
        self.assertEqual(requests.call_count, 3)
        for request in requests.call_args_list:
            self.assertEqual(request.kwargs["model"], "model-router")
        self.assertEqual(
            [request.kwargs["messages"][1]["content"] for request in requests.call_args_list],
            [prompt for _, prompt in demo.PROMPTS],
        )
        self.assertEqual(output.count("Serving model: gpt-4.1-mini-2025-04-14"), 3)
        self.assertIn("input=20, output=10, total=30", output)
        self.assertIn("Elapsed:", output)
        self.assertIn("Paris is the capital of France.", output)

    def run_demo(self, args: list[str]):
        with (
            patch("demo.AzureCliCredential") as credential,
            patch("demo.get_bearer_token_provider") as token_provider,
            patch("demo.AzureOpenAI") as client_factory,
            redirect_stdout(io.StringIO()) as output,
        ):
            requests = client_factory.return_value.__enter__.return_value.chat.completions.create
            requests.return_value = completion()
            demo.main(["--endpoint", "https://example.openai.azure.com/", *args])
            token_provider.assert_called_once_with(
                credential.return_value.__enter__.return_value,
                "https://cognitiveservices.azure.com/.default",
            )
            self.assertEqual(
                client_factory.call_args.kwargs["azure_ad_token_provider"],
                token_provider.return_value,
            )
            return output.getvalue(), requests

    def test_custom_prompt_and_deployment(self) -> None:
        _, requests = self.run_demo(
            ["--prompt", "Explain routing.", "--deployment", "my-router"]
        )
        requests.assert_called_once()
        self.assertEqual(requests.call_args.kwargs["model"], "my-router")
        self.assertEqual(
            requests.call_args.kwargs["messages"][1]["content"], "Explain routing."
        )

    def test_missing_or_invalid_settings_fail_before_authentication(self) -> None:
        cases = (
            [],
            ["--endpoint", "http://example.com"],
            ["--endpoint", "https://example.com", "--prompt", " "],
            ["--endpoint", "https://example.com", "--deployment", " "],
        )
        with patch("demo.AzureCliCredential") as credential, redirect_stderr(io.StringIO()):
            for args in cases:
                with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                    demo.main(args)
                self.assertEqual(error.exception.code, 2)
            credential.assert_not_called()

    def test_environment_configuration(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
                    "AZURE_OPENAI_DEPLOYMENT": "environment-router",
                },
            ),
            patch("demo.AzureCliCredential"),
            patch("demo.get_bearer_token_provider"),
            patch("demo.AzureOpenAI") as factory,
            redirect_stdout(io.StringIO()),
        ):
            request = factory.return_value.__enter__.return_value.chat.completions.create
            request.return_value = completion()
            demo.main(["--prompt", "Hello"])
            self.assertEqual(request.call_args.kwargs["model"], "environment-router")
            self.assertEqual(
                factory.call_args.kwargs["azure_endpoint"],
                "https://example.openai.azure.com/",
            )

    def test_empty_choices_empty_text_and_refusals_are_explicit_errors(self) -> None:
        no_choices = completion()
        no_choices.choices = []
        for response in (no_choices, completion(None), completion(None, "Cannot comply.")):
            with (
                self.subTest(response=response),
                patch("demo.AzureCliCredential"),
                patch("demo.get_bearer_token_provider"),
                patch("demo.AzureOpenAI") as factory,
                redirect_stdout(io.StringIO()),
            ):
                request = factory.return_value.__enter__.return_value.chat.completions.create
                request.return_value = response
                with self.assertRaises(RuntimeError):
                    demo.main(["--endpoint", "https://example.openai.azure.com/"])

    def test_service_errors_are_not_hidden(self) -> None:
        with (
            patch("demo.AzureCliCredential"),
            patch("demo.get_bearer_token_provider"),
            patch("demo.AzureOpenAI") as factory,
            redirect_stdout(io.StringIO()),
        ):
            request = factory.return_value.__enter__.return_value.chat.completions.create
            request.side_effect = TimeoutError("Service timeout")
            with self.assertRaisesRegex(TimeoutError, "Service timeout"):
                demo.main(["--endpoint", "https://example.openai.azure.com/"])


if __name__ == "__main__":
    unittest.main()
