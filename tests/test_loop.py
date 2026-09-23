"""The agent loop, driven by scripted models: every way an episode can end, and
what the trajectory records along the way."""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from env.limitations import load_limitations
from env.loop import Message, ModelResponse, ReactScaffold, SingleShotScaffold, Task, ToolRequest, run_episode
from env.models import OllamaModel, ScriptedModel
from env.tools import Environment
from harness.trace import Termination


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    path = tmp_path_factory.mktemp("snapshot") / "boston.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE crime_incidents AS SELECT 'I' || range AS INCIDENT_NUMBER, 301 AS OFFENSE_CODE FROM range(10)")
    conn.close()
    return Environment(path, load_limitations(), query_timeout_s=3.0)


TASK = Task("c1", "Robberies were down 3% this year.", "Example News", date(2026, 9, 1), date(2026, 9, 23))


def call(name, **arguments) -> ModelResponse:
    return ModelResponse(tool_calls=(ToolRequest(id=f"t-{name}", name=name, arguments=arguments),), model="scripted", tokens_in=100, tokens_out=20)


def run(model, env, *, budget=5, scaffold=None, **kwargs):
    return run_episode(TASK, env, model, scaffold or ReactScaffold(), step_budget=budget, run_id="r", config_hash="h", **kwargs)


def test_complete_episode_records_every_call(env) -> None:
    model = ScriptedModel([
        call("list_tables"),
        call("query", sql="SELECT count(DISTINCT INCIDENT_NUMBER) AS n FROM crime_incidents"),
        call("submit_verdict", verdict="unverifiable", computed_value=10, limitation_ids=["LIM-NO-CROSS-CITY"],
             sql=["SELECT count(DISTINCT INCIDENT_NUMBER) AS n FROM crime_incidents"], reasoning="test"),
    ])
    trajectory = run(model, env)
    assert trajectory.termination == Termination.SUBMITTED
    assert [c.tool for c in trajectory.tool_calls] == ["list_tables", "query", "submit_verdict"]
    assert all(c.ok for c in trajectory.tool_calls)
    assert trajectory.verdict.verdict == "unverifiable"
    assert trajectory.verdict.computed_value == 10.0
    assert trajectory.verdict.limitation_ids == ("LIM-NO-CROSS-CITY",)
    assert len(trajectory.llm_calls) == 3 and trajectory.total_tokens == 360
    # The query's result went back to the model.
    assert '"rows": [[10]]' in model.seen[2][0][-1].content


def test_tool_error_goes_back_to_the_model_and_recovery_is_visible(env) -> None:
    model = ScriptedModel([
        call("query", sql="SELECT * FROM read_csv('/etc/passwd')"),
        call("submit_verdict", verdict="supported"),
    ])
    trajectory = run(model, env)
    assert trajectory.tool_calls[0].ok is False
    assert trajectory.tool_calls[0].error_class.startswith("guard:")
    assert "guard:" in model.seen[1][0][-1].content
    assert trajectory.recovered_from_tool_error


def test_invalid_verdict_does_not_end_the_episode(env) -> None:
    trajectory = run(ScriptedModel([call("submit_verdict", verdict="maybe"), call("submit_verdict", verdict="contradicted")]), env)
    assert trajectory.termination == Termination.SUBMITTED
    assert trajectory.verdict.verdict == "contradicted"
    assert [c.error_class for c in trajectory.tool_calls] == ["bad_verdict", None]


def test_text_only_turn_gets_a_nudge_and_counts_against_the_budget(env) -> None:
    model = ScriptedModel([ModelResponse(content="Thinking..."), call("submit_verdict", verdict="supported")])
    trajectory = run(model, env)
    assert trajectory.termination == Termination.SUBMITTED
    assert len(trajectory.llm_calls) == 2
    assert model.seen[1][0][-1] == Message("user", "Use a tool to inspect the data, or call submit_verdict if you are done.")


def test_budget_exhaustion_forces_a_submit_only_turn(env) -> None:
    model = ScriptedModel([call("list_tables"), call("list_tables"), call("submit_verdict", verdict="underdetermined", spec_sensitive=True)])
    trajectory = run(model, env, budget=2)
    assert trajectory.termination == Termination.SUBMITTED
    assert model.seen[-1][1] == ["submit_verdict"]
    assert "out of steps" in model.seen[-1][0][-1].content


def test_never_submitting_ends_without_a_verdict(env) -> None:
    trajectory = run(ScriptedModel([], fallback=call("list_tables")), env, budget=2)
    assert trajectory.termination == Termination.STEP_BUDGET
    assert trajectory.verdict is None
    # The forced turn offered only submit_verdict, so its list_tables call is refused and recorded.
    assert trajectory.tool_calls[-1].error_class == "tool_not_offered"


def test_model_failure_is_recorded_not_raised(env) -> None:
    class Broken:
        def respond(self, messages, tools):
            raise ConnectionError("provider down")

    trajectory = run(Broken(), env)
    assert trajectory.termination == Termination.FATAL_ERROR
    assert "provider down" in trajectory.error


def test_single_shot_offers_only_submit(env) -> None:
    model = ScriptedModel([call("submit_verdict", verdict="supported")])
    trajectory = run(model, env, scaffold=SingleShotScaffold(), budget=1)
    assert model.seen[0][1] == ["submit_verdict"]
    assert "no data access" in model.seen[0][0][0].content
    assert trajectory.termination == Termination.SUBMITTED


def test_prompt_does_not_give_away_the_limitations(env) -> None:
    """The opening prompt is neutral about how to count: the grain break, the
    exclusions and the rest are for the agent to find in the corpus."""
    opening = ReactScaffold().opening(TASK, env, 12)
    text = " ".join(m.content for m in opening).lower()
    for giveaway in ("incident_number", "distinct", "rape", "domestic", "2019"):
        assert giveaway not in text


def test_prompt_hash_is_stable_and_changes_with_the_conversation(env) -> None:
    first = run(ScriptedModel([call("list_tables"), call("submit_verdict", verdict="supported")]), env)
    second = run(ScriptedModel([call("list_tables"), call("submit_verdict", verdict="supported")]), env)
    assert [c.prompt_hash for c in first.llm_calls] == [c.prompt_hash for c in second.llm_calls]
    assert first.llm_calls[0].prompt_hash != first.llm_calls[1].prompt_hash


class TestOllamaAdapter:
    def test_request_and_response_translation(self, env) -> None:
        sent = {}

        def fake_post(url, payload, timeout):
            sent.update(url=url, payload=payload)
            return {
                "model": "qwen2.5:7b",
                "created_at": "2026-09-23T00:00:00Z",
                "message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "query", "arguments": {"sql": "SELECT 1"}}},
                    {"function": {"name": "submit_verdict", "arguments": '{"verdict": "supported"}'}},
                ]},
                "prompt_eval_count": 812, "eval_count": 34,
            }

        model = OllamaModel("qwen2.5:7b", post_json=fake_post)
        messages = [Message("system", "s"), Message("user", "u"),
                    Message("assistant", "", tool_calls=(ToolRequest("a", "list_tables", {}),)),
                    Message("tool", "[]", tool_call_id="a", name="list_tables")]
        response = model.respond(messages, env.specs())
        assert sent["url"].endswith("/api/chat")
        assert sent["payload"]["options"] == {"temperature": 0.0, "seed": 0}
        assert sent["payload"]["messages"][2]["tool_calls"][0]["function"]["name"] == "list_tables"
        assert sent["payload"]["messages"][3]["tool_name"] == "list_tables"
        assert {t["function"]["name"] for t in sent["payload"]["tools"]} >= {"query", "submit_verdict"}
        assert [c.name for c in response.tool_calls] == ["query", "submit_verdict"]
        assert response.tool_calls[1].arguments == {"verdict": "supported"}  # JSON string decoded
        assert (response.tokens_in, response.tokens_out) == (812, 34)


class TestOpenAICompatibleAdapter:
    def model(self, poster, **kwargs):
        from env.models import OpenAICompatibleModel

        return OpenAICompatibleModel("qwen/qwen3.8-27b:free", provider="openrouter", api_key="sk-test",
                                     post_chat=poster, sleep=lambda s: None, **kwargs)

    RESPONSE = {
        "id": "gen-1", "model": "qwen/qwen3.8-27b:free", "provider": "SomeHost",
        "choices": [{"message": {"content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "query", "arguments": '{"sql": "SELECT 1"}'}}
        ]}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 40, "cost": 0},
    }

    def test_translation_both_ways(self, env) -> None:
        sent = {}

        def poster(url, payload, headers, timeout):
            sent.update(url=url, payload=payload, headers=headers)
            return self.RESPONSE

        messages = [Message("system", "s"), Message("user", "u"),
                    Message("assistant", "", tool_calls=(ToolRequest("a1", "list_tables", {}),)),
                    Message("tool", "[]", tool_call_id="a1", name="list_tables")]
        response = self.model(poster).respond(messages, env.specs())
        assert sent["url"] == "https://openrouter.ai/api/v1/chat/completions"
        assert sent["headers"]["Authorization"] == "Bearer sk-test"
        assert sent["payload"]["messages"][2]["tool_calls"][0]["function"]["arguments"] == "{}"
        assert sent["payload"]["messages"][3]["tool_call_id"] == "a1"
        assert sent["payload"]["usage"] == {"include": True}
        assert response.tool_calls[0] == ToolRequest("call_1", "query", {"sql": "SELECT 1"})
        assert (response.tokens_in, response.tokens_out, response.provider_fingerprint) == (1200, 40, "SomeHost")

    def test_rate_limit_is_retried_then_succeeds(self, env) -> None:
        from env.models import ProviderError

        attempts = []

        def poster(url, payload, headers, timeout):
            attempts.append(1)
            if len(attempts) < 3:
                raise ProviderError("HTTP 429", status=429, retry_after=1)
            return self.RESPONSE

        assert self.model(poster).respond([Message("user", "u")], env.specs()).tool_calls
        assert len(attempts) == 3

    def test_non_retryable_error_raises_and_the_loop_records_it(self, env) -> None:
        from env.models import ProviderError

        def poster(url, payload, headers, timeout):
            raise ProviderError("HTTP 401: bad key", status=401)

        trajectory = run(self.model(poster), env)
        assert trajectory.termination == Termination.FATAL_ERROR and "401" in trajectory.error

    def test_missing_key_is_a_clear_error_and_keys_never_print(self, monkeypatch, tmp_path) -> None:
        from env.models import OpenAICompatibleModel

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            OpenAICompatibleModel("m", provider="openrouter")
        assert "sk-secret" not in repr(OpenAICompatibleModel("m", provider="openrouter", api_key="sk-secret"))

    def test_key_is_read_from_dotenv(self, monkeypatch, tmp_path) -> None:
        from env.models import OpenAICompatibleModel

        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text('GROQ_API_KEY="gsk-from-file"\n')
        model = OpenAICompatibleModel("openai/gpt-oss-120b", provider="groq")
        assert model.api_key == "gsk-from-file" and model.base_url == "https://api.groq.com/openai/v1"
