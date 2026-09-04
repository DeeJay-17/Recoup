import pytest
from recoup_llm import HeuristicLLM, LLMSettings, Message, ToolCall, ToolSchema, Usage, build_router
from recoup_llm.heuristic import last_tool_results
from recoup_llm.types import AssistantTurn


def test_settings_price_lookup_prefers_longest_prefix() -> None:
    s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
    assert s.price_for("gemini-2.5-flash-latest") == (0.30, 2.50)
    assert s.price_for("unknown-model") == (0.0, 0.0)


def test_usage_addition() -> None:
    u = Usage(input_tokens=10, output_tokens=5, cost_usd=0.001) + Usage(
        input_tokens=1, output_tokens=1, cost_usd=0.0005
    )
    assert (u.input_tokens, u.output_tokens, u.cost_usd) == (11, 6, 0.0015)


async def test_heuristic_router_dispatches_by_task() -> None:
    def policy(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
        seen = last_tool_results(messages)
        if "get_invoice" not in seen:
            return AssistantTurn(
                tool_calls=[ToolCall(id="1", name="get_invoice", args={"invoice_ref": "INV-1"})]
            )
        return AssistantTurn(content="done")

    HeuristicLLM.register("unit", policy)
    s = LLMSettings(provider="heuristic", _env_file=None)  # type: ignore[call-arg]
    router = build_router(s)
    llm = router.for_tier("fast")
    assert router.describe()["strong"]["provider"] == "heuristic"
    t1 = await llm.chat([Message(role="user", content="go")], task="unit")
    assert t1.tool_calls[0].name == "get_invoice"
    t2 = await llm.chat(
        [
            Message(role="user", content="go"),
            t1.as_message(),
            Message(role="tool", name="get_invoice", tool_call_id="1", content='{"ok": true}'),
        ],
        task="unit",
    )
    assert t2.content == "done"
    with pytest.raises(RuntimeError):
        await llm.chat([], task="nope")


def test_tool_schema_openai_shape() -> None:
    t = ToolSchema(name="x", description="d", parameters={"type": "object", "properties": {}})
    assert t.as_openai()["function"]["name"] == "x"


def test_tenant_override_changes_model() -> None:
    s = LLMSettings(provider="heuristic", _env_file=None)  # type: ignore[call-arg]
    r = build_router(s, overrides={"strong": {"provider": "heuristic", "model": "x"}})
    assert r.for_tier("strong").provider == "heuristic"
