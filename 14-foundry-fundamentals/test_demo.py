import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, Mock, patch

import demo


class FundamentalsTests(unittest.TestCase):
    def test_denied_endpoint_matches_template(self):
        self.assertEqual(demo.denied_endpoint("https://abc.openai.azure.com/"),
                         "https://abc-deny.openai.azure.com/")

    def test_nonstandard_endpoint_requires_explicit_target(self):
        for endpoint in ("http://abc.openai.azure.com", "https://example.org"):
            with self.assertRaises(ValueError):
                demo.denied_endpoint(endpoint)

    def test_stream_handles_usage_only_chunk_and_empty_delta(self):
        usage = NS(model_dump=lambda: {"total_tokens": 7})
        chunks = [NS(choices=[NS(delta=NS(content="Hello"))], usage=None),
                  NS(choices=[NS(delta=NS(content=None))], usage=None),
                  NS(choices=[], usage=usage)]
        written = []
        self.assertEqual(demo.stream_response(chunks, written.append), {"total_tokens": 7})
        self.assertEqual("".join(written), "Hello")

    def test_empty_stream_does_not_fabricate_usage(self):
        self.assertIsNone(demo.stream_response([], lambda _: None))

    def test_real_sdk_errors_are_not_converted_to_fake_success(self):
        import httpx
        from openai import NotFoundError, PermissionDeniedError
        response = httpx.Response(403, request=httpx.Request("POST", "https://example.org"))
        client = Mock()
        client.chat.completions.create.side_effect = PermissionDeniedError(
            "denied", response=response, body={"error": "missing role"})
        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(demo.probe(client, "alias", 403))
            self.assertFalse(demo.probe(client, "alias", 404))
            client.chat.completions.create.side_effect = NotFoundError(
                "missing", response=httpx.Response(404, request=response.request), body={})
            self.assertTrue(demo.probe(client, "missing", 404))
        self.assertIn("missing role", output.getvalue())

    def test_missing_configuration_is_noninteractive(self):
        with self.assertRaises(SystemExit):
            demo.main(["--endpoint", ""])

    def test_permission_probe_rejects_authentication_and_unrelated_errors(self):
        import httpx
        from openai import APIStatusError

        denial = {
            "code": "PermissionDenied",
            "message": "The principal `restricted` lacks the required data action "
                       "`Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action` "
                       "to perform `POST /openai/v1/chat/completions` operation.",
        }
        cases = [
            (401, denial, True),
            (403, {"error": denial}, True),
            (401, {"code": "PermissionDenied",
                   "message": "Principal does not have access to API/Operation."}, True),
            (401, {"code": "Unauthorized", "message": "Expired token"}, False),
            (401, {"code": "PermissionDenied", "message": "Invalid credential"}, False),
            (403, {"code": "Forbidden", "message": "Blocked by network policy"}, False),
            (403, {"code": "PermissionDenied", "message": "lacks the required data action Other/action"}, False),
            (404, denial, False),
            (429, denial, False),
            (401, None, False),
            (401, {"error": "Unauthorized"}, False),
        ]
        for status, body, expected in cases:
            with self.subTest(status=status, body=body), redirect_stdout(StringIO()):
                client = Mock()
                response = httpx.Response(status, request=httpx.Request("POST", "https://example.org"))
                client.chat.completions.create.side_effect = APIStatusError(
                    "service error", response=response, body=body,
                )
                self.assertEqual(demo.probe(client, "alias"), expected)
        with redirect_stdout(StringIO()):
            self.assertFalse(demo.probe(Mock(), "alias"))

    def test_partial_certificate_configuration_fails_before_azure(self):
        with patch.dict("os.environ", {}, clear=True):
            for arguments in (["--client-id", "client"],
                              ["--client-id", "client", "--tenant-id", "tenant"],
                              ["--certificate-path", ""],
                              ["--tenant-id", "", "--client-id", "client", "--certificate-path", "cert.pem"]):
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                    demo.main(["--endpoint", "https://primary.openai.azure.com", *arguments])

    def test_explicit_identity_is_used_for_both_accounts_without_default_fallback(self):
        for certificate_error in (False, True):
            with (
                patch.dict("os.environ", {}, clear=True),
                patch("azure.identity.CertificateCredential") as certificate,
                patch("azure.identity.DefaultAzureCredential") as default,
                patch("azure.identity.get_bearer_token_provider", return_value="certificate-token") as provider,
                patch("openai.OpenAI") as factory,
                patch("demo.probe", return_value=True) as probe,
                redirect_stdout(StringIO()),
            ):
                if certificate_error:
                    certificate.side_effect = ValueError("invalid certificate")
                clients = [MagicMock(), MagicMock()]
                factory.side_effect = clients
                client = clients[0].__enter__.return_value
                client.chat.completions.create.return_value.__enter__.return_value = []
                arguments = [
                    "--endpoint", "https://primary.openai.azure.com",
                    "--denied-endpoint", "https://denied.openai.azure.com",
                    "--tenant-id", "tenant", "--client-id", "client", "--certificate-path", "cert.pem",
                ]
                if certificate_error:
                    with self.assertRaisesRegex(ValueError, "invalid certificate"):
                        demo.main(arguments)
                    factory.assert_not_called()
                else:
                    self.assertEqual(demo.main(arguments), 0)
                    certificate.assert_called_once_with(
                        tenant_id="tenant", client_id="client", certificate_path="cert.pem",
                    )
                    provider.assert_called_once_with(
                        certificate.return_value, "https://cognitiveservices.azure.com/.default",
                    )
                    self.assertEqual([call.kwargs["api_key"] for call in factory.call_args_list],
                                     ["certificate-token", "certificate-token"])
                    self.assertEqual(probe.call_args_list[0].args[2], 404)
                    self.assertEqual(len(probe.call_args_list[1].args), 2)
                default.assert_not_called()


if __name__ == "__main__":
    unittest.main()
