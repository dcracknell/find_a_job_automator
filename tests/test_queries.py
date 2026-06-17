from __future__ import annotations

import sys
from contextlib import contextmanager
from types import SimpleNamespace

from job_search.profile import queries as queries_mod


def _profile() -> dict:
    return {
        "location": {"city": "Sheffield"},
        "remote_ok": True,
        "core_skills": ["Python", "SQL"],
        "adjacent_skills": ["Excel"],
        "negative_signals": {
            "title_excludes": ["senior", "lead"],
            "company_blocklist": ["Bad Co"],
        },
        "target_roles": {
            "core": ["Data Analyst"],
            "adjacent": ["Reporting Analyst"],
            "stretch": ["Analytics Engineer"],
        },
        "filters": {"exclude_companies": ["Nope Ltd"]},
    }


def test_generate_queries_falls_back_without_settings() -> None:
    generated = queries_mod.generate_queries(_profile())

    assert "Data Analyst" in generated
    assert "Data Analyst Sheffield" in generated
    assert "Data Analyst remote" in generated
    assert not any("senior" in query.lower() for query in generated)


def test_generate_queries_prefers_claude_when_configured(monkeypatch) -> None:
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=(
                            '["Python SQL analyst Sheffield", '
                            '"senior data analyst", '
                            '"Remote reporting analyst"]'
                        )
                    )
                ],
                usage=SimpleNamespace(
                    input_tokens=123,
                    cache_read_input_tokens=0,
                    output_tokens=45,
                ),
            )

    class FakeClient:
        messages = FakeMessages()

    @contextmanager
    def fake_api_call_wrapper(operation: str):
        rec = {"operation": operation}
        yield rec
        assert rec["model"] == "claude-test"
        assert rec["input_tokens"] == 123
        assert rec["output_tokens"] == 45

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-real-looking-value")
    monkeypatch.setattr(queries_mod, "api_call_wrapper", fake_api_call_wrapper)
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=lambda api_key: FakeClient()),
    )

    settings = {
        "models": {
            "queries": {
                "use_claude": True,
                "model": "claude-test",
                "max_tokens_response": 200,
                "max_queries": 5,
            }
        }
    }

    generated = queries_mod.generate_queries(_profile(), settings, "prefer analyst roles")

    assert generated[:2] == ["Python SQL analyst Sheffield", "Remote reporting analyst"]
    assert not any("senior" in query.lower() for query in generated)
    assert calls
    assert calls[0]["model"] == "claude-test"
    assert "prefer analyst roles" in calls[0]["messages"][0]["content"]
