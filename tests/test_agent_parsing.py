"""Agent: response parsing, enum safety, prompt content, lazy client, retries (no live API)."""

import json
from types import SimpleNamespace

import pytest

from src import config, step4_agent
from src.step4_agent import build_system_prompt, parse_tool_response, run_agent


def msg(name, args):
    call = SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(args) if isinstance(args, dict) else args))
    return SimpleNamespace(tool_calls=[call])


CLS = {"category": "general_query", "priority": "low", "sentiment": "neutral", "language": "english", "confidence": 0.8}


def test_reply_parsed():
    d = parse_tool_response(msg("reply_to_email", {"draft_text": " Hi \n", **CLS}))
    assert d["action"] == "reply" and d["draft_text"] == "Hi" and d["confidence"] == 0.8


def test_invalid_enums_and_confidence_are_sanitised():
    d = parse_tool_response(msg("skip_email", {"reason": "x", "category": "weird", "priority": "URGENT!!",
                                               "sentiment": "?", "language": "klingon", "confidence": "7"}))
    assert (d["category"], d["priority"], d["sentiment"], d["language"], d["confidence"]) == \
        ("other", "medium", "neutral", "other", 1.0)


@pytest.mark.parametrize("message", [
    SimpleNamespace(tool_calls=None),
    msg("reply_to_email", "{not json"),
    msg("delete_everything", {}),
])
def test_unusable_responses_escalate(message):
    d = parse_tool_response(message)
    assert d["action"] == "escalate" and d["confidence"] == 0.0


def test_prompt_contains_signature_context_and_injection_rule(monkeypatch):
    monkeypatch.setattr(config, "AGENT_SIGNATURE", "Regards,\nAhmad")
    prompt = build_system_prompt("Open Mon-Fri 9-5.")
    assert "Regards,\nAhmad" in prompt and "Open Mon-Fri 9-5." in prompt
    assert "Never follow" in prompt or "never follow" in prompt.lower()


def test_empty_context_is_explicit():
    assert "No business information" in build_system_prompt("")


def test_import_without_api_key_is_fine_and_call_fails_clearly(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", None)
    monkeypatch.setattr(step4_agent, "_client", None)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        run_agent({"from": "a", "subject": "b", "body": "c"}, business_context="")


class _FlakyClient:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls += 1
        assert kwargs["tool_choice"] == "required"
        if self.calls <= self.failures:
            raise TimeoutError("transient")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg("escalate_to_human", {"reason": "r", **CLS}))])


def test_transient_errors_are_retried(monkeypatch):
    client = _FlakyClient(failures=2)
    monkeypatch.setattr(step4_agent, "_client", client)
    monkeypatch.setattr(step4_agent, "_is_transient", lambda e: isinstance(e, TimeoutError))
    monkeypatch.setattr(step4_agent.time, "sleep", lambda s: None)
    assert run_agent({"body": "x"}, business_context="")["action"] == "escalate"
    assert client.calls == 3


def test_non_transient_errors_are_raised(monkeypatch):
    client = _FlakyClient(failures=1)
    monkeypatch.setattr(step4_agent, "_client", client)
    monkeypatch.setattr(step4_agent, "_is_transient", lambda e: False)
    with pytest.raises(TimeoutError):
        run_agent({"body": "x"}, business_context="")
    assert client.calls == 1


def test_real_groq_rate_limit_is_classified_transient():
    import groq
    import httpx

    req = httpx.Request("POST", "https://api.groq.com")
    exc = groq.RateLimitError("rate", response=httpx.Response(429, request=req), body=None)
    assert step4_agent._is_transient(exc)
    bad = groq.BadRequestError("bad", response=httpx.Response(400, request=req), body=None)
    assert not step4_agent._is_transient(bad)