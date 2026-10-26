import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import demo


class TracingTests(unittest.TestCase):
    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer("test")
        self.client = Mock()
        self.client.chat.completions.create.return_value = SimpleNamespace(
            id="response-123", usage=SimpleNamespace(prompt_tokens=8, completion_tokens=4),
        )

    def tearDown(self):
        self.provider.shutdown()

    def test_real_span_parentage_usage_and_tool_failure(self):
        with patch("demo.sleep") as sleep:
            trace_id = demo.run_agent(self.client, self.tracer, "deployed-model", "slow", 3)
            sleep.assert_called_once_with(3)
        spans = self.exporter.get_finished_spans()
        root = next(span for span in spans if span.name == "workshop-agent")
        self.assertEqual({span.context.trace_id for span in spans}, {int(trace_id, 16)})
        self.assertEqual(len(spans), 3)
        for span in spans[:2]:
            self.assertEqual(span.parent.span_id, root.context.span_id)
        self.assertEqual(spans[1].attributes["gen_ai.usage.input_tokens"], 8)
        self.assertEqual(self.client.chat.completions.create.call_args.kwargs["model"], "deployed-model")
        demo.run_agent(self.client, self.tracer, "model", "failed", 0)
        tool, model, root = self.exporter.get_finished_spans()[-3:]
        self.assertEqual(tool.status.status_code, StatusCode.ERROR)
        self.assertEqual(tool.attributes["error.type"], "SyntheticToolFailure")
        self.assertEqual(tool.attributes["workshop.error_type"], "SyntheticToolFailure")
        self.assertEqual(model.status.status_code, StatusCode.OK)
        self.assertTrue(root.attributes["workshop.recovered_tool_failure"])

    def test_exception_secrets_are_not_in_spans_or_events(self):
        self.client.chat.completions.create.side_effect = ValueError("SECRET-TOKEN private prompt")
        with self.assertRaisesRegex(RuntimeError, "Model request failed"):
            demo.run_agent(self.client, self.tracer, "model", "normal")
        spans = self.exporter.get_finished_spans()
        self.assertNotIn("SECRET-TOKEN", repr([(s.attributes, s.events, s.status.description) for s in spans]))
        self.assertTrue(all(not span.events for span in spans))
        self.assertEqual(spans[-1].status.status_code, StatusCode.ERROR)
        self.assertEqual(spans[-2].attributes["workshop.error_type"], "ModelRequestFailure")
        self.assertFalse(any("content" in key or "authorization" in key
                             for span in spans for key in span.attributes))

    def test_exporter_uses_the_actual_provider_for_resource_metadata(self):
        with patch("sys.argv", ["demo.py", "--endpoint", "https://model.example",
                               "--connection-string", "synthetic"]), \
                patch("demo.DefaultAzureCredential"), patch("demo.AzureOpenAI"), \
                patch("demo.get_bearer_token_provider"), patch("demo.run_agent"), \
                patch("demo.TracerProvider") as provider, \
                patch("demo.BatchSpanProcessor"), \
                patch("azure.monitor.opentelemetry.exporter.AzureMonitorTraceExporter") as exporter:
            demo.main()
        self.assertIs(exporter.call_args.kwargs["tracer_provider"], provider.return_value)
        self.assertTrue(exporter.call_args.kwargs["disable_offline_storage"])
        provider.return_value.force_flush.assert_called_once_with(timeout_millis=30000)
        provider.return_value.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
