import json
import os
import unittest
from unittest.mock import patch

from src.ai_harness import (
    CLAIM_FACT,
    AnalysisTask,
    EvidenceReference,
    ModelAdapterError,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)


class FakePost:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, url, *, headers, payload, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response


class OpenAICompatibleAdapterTest(unittest.TestCase):
    def test_request_and_structured_response(self):
        post = FakePost(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "content": "分析完成",
                                    "data": {"score": 1},
                                    "citations": ["evidence:1"],
                                    "claims": [
                                        {
                                            "claim_type": "fact",
                                            "text": "事实",
                                            "evidence_ids": ["evidence:1"],
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ]
            }
        )
        adapter = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                base_url="https://example.com/v1",
                model="test-model",
                api_key="secret-key",
                timeout_seconds=12,
            ),
            post_function=post,
        )

        output = adapter.generate(_task(), (_evidence(),))

        self.assertEqual(output.content, "分析完成")
        self.assertEqual(output.claims[0].claim_type, CLAIM_FACT)
        self.assertEqual(
            post.calls[0]["url"],
            "https://example.com/v1/chat/completions",
        )
        self.assertEqual(
            post.calls[0]["headers"]["Authorization"],
            "Bearer secret-key",
        )
        self.assertNotIn(
            "secret-key",
            json.dumps(post.calls[0]["payload"]),
        )
        self.assertEqual(post.calls[0]["timeout_seconds"], 12)

    def test_local_service_does_not_require_api_key(self):
        post = FakePost(_response({"content": "本地结果"}))
        adapter = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                base_url="http://localhost:8000/v1",
                model="local-model",
            ),
            post_function=post,
        )

        adapter.generate(_task(), (_evidence(),))

        self.assertNotIn("Authorization", post.calls[0]["headers"])

    def test_invalid_json_and_http_error_are_wrapped(self):
        invalid = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                base_url="https://example.com/v1",
                model="test",
            ),
            post_function=FakePost(
                {
                    "choices": [
                        {"message": {"content": "not json"}}
                    ]
                }
            ),
        )
        failing = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                base_url="https://example.com/v1",
                model="test",
            ),
            post_function=FakePost(error=TimeoutError("timeout")),
        )

        with self.assertRaises(ModelAdapterError):
            invalid.generate(_task(), (_evidence(),))
        with self.assertRaises(ModelAdapterError) as context:
            failing.generate(_task(), (_evidence(),))
        self.assertIn("TimeoutError", str(context.exception))

    def test_config_reads_environment_without_persisting_it(self):
        with patch.dict(
            os.environ,
            {
                "WEALTH_FARM_AI_BASE_URL": "https://example.com/v1",
                "WEALTH_FARM_AI_API_KEY": "env-secret",
                "WEALTH_FARM_AI_MODEL": "env-model",
            },
            clear=False,
        ):
            config = OpenAICompatibleConfig.from_env()

        self.assertEqual(config.model, "env-model")
        self.assertEqual(config.api_key, "env-secret")


def _task():
    return AnalysisTask(
        task_id="task:1",
        user_id="user:default",
        asset_id="asset:1",
        task_type="asset_analysis",
        question="分析标的",
        status="context_ready",
    )


def _evidence():
    return EvidenceReference(
        evidence_id="evidence:1",
        evidence_type="factor",
        source_id="factor:1",
        title="动量",
        content="0.1",
    )


def _response(payload):
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(payload)
                }
            }
        ]
    }


if __name__ == "__main__":
    unittest.main()
