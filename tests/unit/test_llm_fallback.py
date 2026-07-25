from delta_chat.chat.llm import FallbackLLMClient


class BrokenClient:
    provider = "litellm"

    def answer(self, prompt: str, *, system: str = ""):
        raise RuntimeError("rate limited")


class WorkingClient:
    provider = "extractive"

    def answer(self, prompt: str, *, system: str = ""):
        return {
            "answer": "grounded fallback",
            "citations": ["D:item"],
            "confidence": "medium",
            "unsupported": False,
        }


def test_hosted_provider_failure_uses_labeled_extractive_fallback():
    client = FallbackLLMClient(BrokenClient(), WorkingClient())

    result = client.answer("question")

    assert result["answer"] == "grounded fallback"
    assert client.provider == "extractive_fallback"
