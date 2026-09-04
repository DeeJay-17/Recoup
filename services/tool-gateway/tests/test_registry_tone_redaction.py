import pytest
from pydantic import BaseModel
from recoup_tool_gateway import tools  # noqa: F401
from recoup_tool_gateway.redaction import redact
from recoup_tool_gateway.registry import ToolRegistry, ToolSpec, registry
from recoup_tool_gateway.tools.comm_tools import score_tone


def test_manifest_has_expected_tools_and_flags() -> None:
    names = {t.name for t in registry.all()}
    assert {
        "get_invoice",
        "reconcile_lines",
        "propose_action",
        "send_email",
        "create_credit_memo",
        "classify_tone",
    } <= names
    send = registry.get("send_email")
    assert (
        send
        and send.side_effect
        and send.requires_policy_check
        and send.action_type == "SEND_EMAIL"
    )
    inv = registry.get("get_invoice")
    assert inv and not inv.side_effect and not inv.requires_policy_check
    entry = send.manifest_entry()
    assert entry["parameters"]["properties"]["to"]["type"] == "array"


def test_least_privilege_manifest_hides_mutations_without_context() -> None:
    names = {t["name"] for t in registry.manifest(case=None, customer=None)}
    assert "create_credit_memo" not in names
    assert "apply_payment_plan" in names  # visible unless credit hold
    names = {
        t["name"]
        for t in registry.manifest(
            case={"root_cause": "DISPUTE_PRICING"}, customer={"credit_hold": True}
        )
    }
    assert "create_credit_memo" in names
    assert "apply_payment_plan" not in names


def test_registry_rejects_unchecked_side_effects() -> None:
    class A(BaseModel):
        pass

    async def h(ctx: object, args: A) -> A:
        return A()

    r = ToolRegistry()
    with pytest.raises(ValueError):
        r.register(
            ToolSpec(
                name="x", description="", args_model=A, result_model=A, handler=h, side_effect=True
            )
        )
    with pytest.raises(ValueError):
        r.register(
            ToolSpec(
                name="y",
                description="",
                args_model=A,
                result_model=A,
                handler=h,
                requires_policy_check=True,
            )
        )


def test_tone_scoring() -> None:
    good, flags = score_tone(
        "Hi Jane, thank you for your note. Could you please share the PO number when you have a moment? We appreciate it."
    )
    assert good >= 0.85 and flags == []
    bad, flags = score_tone(
        "FINAL NOTICE!!! You must pay immediately or face legal action. This is unacceptable!"
    )
    assert bad < 0.5
    assert any("aggressive" in f for f in flags)


def test_redaction_masks_phones_not_emails() -> None:
    out = redact(
        {"contact": "Jane <jane@acme-demo.com> +1 (415) 555-0134", "ssn": "123-45-6789", "n": 3}
    )
    assert "jane@acme-demo.com" in out["contact"]
    assert "555" not in out["contact"]
    assert out["ssn"] == "[ssn]"
    assert out["n"] == 3
