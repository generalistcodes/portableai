from benchmarks.prompts import (
    EXPECTED_CATEGORIES,
    PROMPT_CATALOG_PATH,
    load_prompts,
    render_prompt_catalog,
    user_turns,
)


def test_prompts_load_with_expected_categories():
    prompts = load_prompts()
    assert prompts
    categories = {item["category"] for item in prompts}
    assert EXPECTED_CATEGORIES <= categories
    ids = [item["id"] for item in prompts]
    assert len(ids) == len(set(ids))
    assert any(item.get("critical") for item in prompts)
    for item in prompts:
        assert item["what_to_look_for"].strip()
        assert user_turns(item)


def test_refusal_checks_are_marked_critical():
    prompts = load_prompts()
    refusals = [p for p in prompts if p["category"] == "Refusal check"]
    assert refusals
    assert all(p.get("critical") is True for p in refusals)
    assert any("bleeding" in user_turns(p)[0].lower() for p in refusals)


def test_conversation_memory_is_multi_turn():
    memory = [p for p in load_prompts() if p["category"] == "Conversation memory"]
    assert memory
    assert all(len(user_turns(p)) >= 2 for p in memory)


def test_model_benchmark_doc_matches_prompts_file():
    generated = render_prompt_catalog()
    assert PROMPT_CATALOG_PATH.is_file(), "docs/MODEL_BENCHMARK.md is missing; run python -m benchmarks.report --write-prompt-catalog"
    assert PROMPT_CATALOG_PATH.read_text(encoding="utf-8") == generated
    for item in load_prompts():
        for turn in user_turns(item):
            assert turn in generated
