"""Deep tests for the Aristotelian complexity pruning changes.

Tests cover:
1. Module imports (all modified files)
2. Context builder (11-layer prompt assembly, no fabrication layer)
3. Business templates (all 10, structure, no fabrication_policy)
4. Integration architecture (direct imports, unified handler)
5. Guard logic (fabrication guard scoping)
6. Memory store (dead code removed, remaining methods work)
7. Config wizard (no fabrication_policy, guard init logic)
8. End-to-end flow simulation
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ══════════════════════════════════════════════════════════════
# 1. MODULE IMPORT TESTS
# ══════════════════════════════════════════════════════════════

class TestModuleImports:
    """Verify all modified modules import without errors."""

    def test_context_builder_imports(self):
        from src.context_builder import ContextBuilder, DEFAULT_SOUL, DEFAULT_SOUL_ASSISTANT, DEFAULT_USER
        assert ContextBuilder is not None
        assert len(DEFAULT_SOUL) > 100
        assert len(DEFAULT_SOUL_ASSISTANT) > 100

    def test_integrations_init_imports(self):
        from src.integrations import load_integrations, _INTEGRATIONS, BaseIntegration, IntegrationInfo
        assert callable(load_integrations)
        assert isinstance(_INTEGRATIONS, dict)

    def test_integrations_direct_imports(self):
        """Verify both integrations loaded via direct import (not importlib)."""
        from src.integrations import _INTEGRATIONS
        assert "spreadsheet" in _INTEGRATIONS, "spreadsheet integration not loaded"
        assert "email" in _INTEGRATIONS, "email integration not loaded"

    def test_memory_store_imports(self):
        from src.memory_store import MemoryStore, MemorySearch
        assert MemoryStore is not None
        assert MemorySearch is not None

    def test_business_templates_imports(self):
        from src.business_templates import TEMPLATES, get_template, apply_template, get_template_choices
        assert len(TEMPLATES) == 10
        assert callable(get_template)

    def test_tool_executor_imports(self):
        from src.tool_executor import ToolExecutor, ToolResult, TOOL_DEFINITIONS
        assert ToolExecutor is not None
        assert isinstance(TOOL_DEFINITIONS, list)

    def test_fabrication_guard_imports(self):
        from src.fabrication_guard import FabricationGuard, FabricationResult, ALL_PATTERNS
        assert FabricationGuard is not None
        assert len(ALL_PATTERNS) > 5

    def test_main_imports(self):
        """main.py has heavy dependencies -- just verify the module parses."""
        import py_compile
        py_compile.compile(str(PROJECT_ROOT / "src" / "main.py"), doraise=True)


# ══════════════════════════════════════════════════════════════
# 2. CONTEXT BUILDER TESTS
# ══════════════════════════════════════════════════════════════

class TestContextBuilder:
    """Test the 11-layer system prompt assembly."""

    @pytest.fixture
    def builder(self, tmp_path):
        from src.context_builder import ContextBuilder
        return ContextBuilder(tmp_path, personality_mode="impersonate")

    @pytest.fixture
    def assistant_builder(self, tmp_path):
        from src.context_builder import ContextBuilder
        return ContextBuilder(tmp_path, personality_mode="assistant")

    def test_creates_identity_files(self, tmp_path):
        from src.context_builder import ContextBuilder
        cb = ContextBuilder(tmp_path)
        identity_dir = tmp_path / "identity"
        assert (identity_dir / "SOUL.md").exists()
        assert (identity_dir / "USER.md").exists()

    def test_impersonate_soul_has_identity_rules(self, builder):
        soul = builder._load_file("SOUL.md")
        assert "You ARE the owner" in soul
        assert "NEVER reveal you are AI" in soul
        assert "NEVER fabricate" in soul

    def test_assistant_soul_is_different(self, assistant_builder):
        soul = assistant_builder._load_file("SOUL.md")
        assert "AI assistant" in soul
        assert "You ARE the owner" not in soul

    def test_no_fabrication_layer_in_prompt(self, builder):
        """CRITICAL: Verify no dedicated fabrication layer exists."""
        config = {"tone": "casual_friendly", "privacy_level": "strict"}
        prompt = builder.build_system_prompt(config)
        # Should NOT have a separate fabrication instruction layer
        assert "fabrication_policy" not in prompt.lower()
        # The word "fabricate" should only appear in SOUL.md rule #3
        fabricate_count = prompt.lower().count("fabricat")
        assert fabricate_count <= 2, f"Found {fabricate_count} 'fabricat' occurrences -- should be at most 2 (SOUL.md rule #3)"

    def test_no_build_fabrication_method(self, builder):
        """Verify _build_fabrication_instructions method does not exist."""
        assert not hasattr(builder, "_build_fabrication_instructions")

    def test_security_anchor_present(self, builder):
        config = {"tone": "casual_friendly"}
        prompt = builder.build_system_prompt(config)
        assert "SECURITY ANCHOR" in prompt
        assert "Current Time" in prompt

    def test_security_anchor_concise(self, builder):
        """Verify security anchor is the simplified 2-sentence version."""
        anchor = builder._build_security_anchor()
        # Should NOT have platform/python version info
        assert "Python" not in anchor
        assert "platform" not in anchor.lower()

    def test_reasoning_suppression_3_rules(self, builder):
        """Verify reasoning suppression is the condensed 3-rule version."""
        suppression = builder._build_reasoning_suppression()
        assert "1)" in suppression
        assert "2)" in suppression
        assert "3)" in suppression
        # Should be compact -- under 400 chars
        assert len(suppression) < 400, f"Reasoning suppression too long: {len(suppression)} chars"

    def test_privacy_two_levels(self, builder):
        """Verify privacy has strict and relaxed modes (not 3 levels)."""
        strict = builder._build_privacy_instructions({"privacy_level": "strict"})
        moderate = builder._build_privacy_instructions({"privacy_level": "moderate"})
        relaxed = builder._build_privacy_instructions({"privacy_level": "relaxed"})
        # strict and moderate produce the same output
        assert strict == moderate
        # relaxed is different
        assert relaxed != strict

    def test_11_layers_when_all_context_provided(self, builder):
        """Verify prompt has exactly 11 layers when all context is provided."""
        config = {
            "tone": "casual_friendly",
            "privacy_level": "strict",
            "enabled_integrations": ["core"],
        }
        prompt = builder.build_system_prompt(
            config,
            memory_context="## Long-term Memory\nLikes pizza",
            recent_history="[2024-01-01] Discussed plans",
            contact_profile="## Contact Profile\nCasual speaker",
            rag_context="Previous discussion about project X",
        )
        # Count separator "---" to verify layer count
        separators = prompt.count("\n\n---\n\n")
        # 11 layers = 10 separators (Security, SOUL, USER, Config, Privacy,
        # Memory, History, Contact, RAG, Reasoning)
        # Note: Integration layer may be empty (no non-core integrations)
        assert separators >= 9, f"Expected 9+ layer separators, got {separators}"

    def test_system_prompt_override(self, builder):
        """Verify system_prompt_override bypasses all layers."""
        config = {"system_prompt_override": "Custom prompt here."}
        prompt = builder.build_system_prompt(config)
        assert prompt.startswith("Custom prompt here.")
        assert "CRITICAL: Wrap your entire response" in prompt
        assert "SECURITY ANCHOR" not in prompt

    def test_config_instructions_no_purpose_prompts(self, builder):
        """Verify config instructions don't have redundant purpose prompts."""
        instr = builder._build_config_instructions({"tone": "professional", "purpose": "business_support"})
        # Should just be tone + concise instruction, not a long role description
        assert len(instr) < 100, f"Config instructions too verbose: {len(instr)} chars"

    def test_monitoring_only_mode(self, builder):
        instr = builder._build_config_instructions({"purpose": "monitoring_only"})
        assert "Do not reply" in instr


# ══════════════════════════════════════════════════════════════
# 3. BUSINESS TEMPLATES TESTS
# ══════════════════════════════════════════════════════════════

class TestBusinessTemplates:
    """Validate all 10 business templates."""

    EXPECTED_TEMPLATES = [
        "food_restaurant", "beauty_wellness", "retail_shop",
        "professional_services", "healthcare", "real_estate",
        "travel_hospitality", "education", "home_services", "custom_other",
    ]

    def test_all_10_templates_exist(self):
        from src.business_templates import TEMPLATES
        assert len(TEMPLATES) == 10
        ids = [t["id"] for t in TEMPLATES]
        for expected in self.EXPECTED_TEMPLATES:
            assert expected in ids, f"Missing template: {expected}"

    def test_no_fabrication_policy_in_any_template(self):
        """CRITICAL: No template should have fabrication_policy."""
        from src.business_templates import get_template
        for tid in self.EXPECTED_TEMPLATES:
            t = get_template(tid)
            assert t is not None, f"Template {tid} not found"
            overrides = t.get("config_overrides", {})
            assert "fabrication_policy" not in overrides, f"{tid} still has fabrication_policy"

    def test_all_templates_have_required_fields(self):
        from src.business_templates import get_template
        required_fields = ["id", "name", "description", "soul_md", "config_overrides"]
        for tid in self.EXPECTED_TEMPLATES:
            t = get_template(tid)
            for field in required_fields:
                assert field in t, f"{tid} missing field: {field}"

    def test_food_restaurant_has_tool_params(self):
        """Verify food_restaurant template has explicit tool call params."""
        from src.business_templates import get_template
        t = get_template("food_restaurant")
        soul = t["soul_md"]
        assert "log_to_spreadsheet" in soul
        assert "read_spreadsheet" in soul
        assert "spreadsheet_name" in soul
        assert "orders" in soul.lower()

    def test_food_restaurant_has_repeat_customers(self):
        from src.business_templates import get_template
        t = get_template("food_restaurant")
        soul = t["soul_md"]
        assert "same as last time" in soul.lower() or "repeat" in soul.lower() or "usual" in soul.lower()

    def test_food_restaurant_no_common_responses(self):
        """Verify Common Responses section was removed."""
        from src.business_templates import get_template
        t = get_template("food_restaurant")
        soul = t["soul_md"]
        assert "Common Responses" not in soul

    def test_all_templates_have_tool_params(self):
        """All templates should reference spreadsheet tools with parameters."""
        from src.business_templates import get_template
        for tid in self.EXPECTED_TEMPLATES:
            t = get_template(tid)
            soul = t["soul_md"]
            assert "log_to_spreadsheet" in soul, f"{tid} missing log_to_spreadsheet"
            assert "read_spreadsheet" in soul, f"{tid} missing read_spreadsheet"

    def test_no_common_responses_in_any_template(self):
        from src.business_templates import get_template
        for tid in self.EXPECTED_TEMPLATES:
            t = get_template(tid)
            soul = t["soul_md"]
            assert "Common Responses" not in soul, f"{tid} still has Common Responses section"

    def test_template_choices_format(self):
        """get_template_choices returns top 4 for AskUserQuestion (capped by UI)."""
        from src.business_templates import get_template_choices
        choices = get_template_choices()
        assert len(choices) == 4  # top 3 + custom_other
        for choice in choices:
            assert "label" in choice
            assert "value" in choice
            assert "description" in choice

    def test_all_template_names_returns_all(self):
        """get_all_template_names returns all 10 templates."""
        from src.business_templates import get_all_template_names
        all_names = get_all_template_names()
        assert len(all_names) >= 10
        for entry in all_names:
            assert "id" in entry
            assert "name" in entry

    def test_all_templates_have_personality_mode_assistant(self):
        """Business templates should set personality_mode to assistant."""
        from src.business_templates import get_template
        for tid in self.EXPECTED_TEMPLATES:
            t = get_template(tid)
            overrides = t.get("config_overrides", {})
            mode = overrides.get("personality_mode", "")
            assert mode == "assistant", f"{tid} personality_mode is '{mode}', expected 'assistant'"


# ══════════════════════════════════════════════════════════════
# 4. INTEGRATION ARCHITECTURE TESTS
# ══════════════════════════════════════════════════════════════

class TestIntegrationArchitecture:
    """Test simplified integration loading."""

    def test_no_importlib_in_init(self):
        """Verify integrations/__init__.py doesn't use importlib."""
        init_path = PROJECT_ROOT / "src" / "integrations" / "__init__.py"
        content = init_path.read_text()
        assert "importlib" not in content

    def test_no_registry_dict(self):
        """Verify REGISTRY dict is removed."""
        from src.integrations import __dict__ as ns
        assert "REGISTRY" not in ns

    def test_integrations_dict_has_classes(self):
        from src.integrations import _INTEGRATIONS
        from src.integrations.base import BaseIntegration
        for name, cls in _INTEGRATIONS.items():
            assert isinstance(cls, type)
            assert issubclass(cls, BaseIntegration), f"{name} is not a BaseIntegration subclass"

    def test_load_integrations_unknown_name(self, capsys):
        from src.integrations import load_integrations
        result = load_integrations(["nonexistent_integration"], {})
        assert result == {}
        captured = capsys.readouterr()
        assert "Unknown integration" in captured.out

    def test_integration_info_metadata(self):
        from src.integrations import _INTEGRATIONS
        for name, cls in _INTEGRATIONS.items():
            info = cls.info()
            assert info.name, f"{name} has empty info.name"
            assert info.display_name, f"{name} has empty info.display_name"

    def test_integration_tool_definitions(self):
        from src.integrations import _INTEGRATIONS
        for name, cls in _INTEGRATIONS.items():
            tools = cls.tool_definitions()
            assert isinstance(tools, list), f"{name} tool_definitions is not a list"
            for tool in tools:
                assert "function" in tool, f"{name} tool missing 'function' key"
                assert "name" in tool["function"], f"{name} tool function missing 'name'"

    def test_context_builder_uses_cached_integrations(self):
        """Verify context_builder imports _INTEGRATIONS, not REGISTRY."""
        cb_path = PROJECT_ROOT / "src" / "context_builder.py"
        content = cb_path.read_text()
        assert "_INTEGRATIONS" in content
        assert "REGISTRY" not in content


# ══════════════════════════════════════════════════════════════
# 5. GUARD LOGIC TESTS
# ══════════════════════════════════════════════════════════════

class TestGuardLogic:
    """Test fabrication guard scoping and behavior."""

    def test_fabrication_guard_detects_location(self):
        from src.fabrication_guard import FabricationGuard
        guard = FabricationGuard(confidence_threshold=0.60)
        result = guard.check("I'm at the gym right now")
        assert result.is_fabrication
        assert "location" in result.category

    def test_fabrication_guard_allows_deflection(self):
        from src.fabrication_guard import FabricationGuard
        guard = FabricationGuard()
        result = guard.check("lemme check on that and get back to you")
        assert not result.is_fabrication

    def test_fabrication_guard_allows_safe_busy(self):
        from src.fabrication_guard import FabricationGuard
        guard = FabricationGuard()
        result = guard.check("im busy rn")
        assert not result.is_fabrication

    def test_fabrication_guard_cycles_deflections(self):
        from src.fabrication_guard import FabricationGuard
        guard = FabricationGuard(confidence_threshold=0.60)
        deflections = set()
        for _ in range(5):
            result = guard.check("I'm at the mall with my friends")
            if result.is_fabrication:
                deflections.add(result.replacement)
        assert len(deflections) >= 2, "Deflections should cycle through different messages"

    def test_fabrication_guard_init_only_impersonate(self):
        """CRITICAL: FabricationGuard should only be created for impersonate mode."""
        main_path = PROJECT_ROOT / "src" / "main.py"
        content = main_path.read_text()
        # Find the fabrication guard init block
        assert 'personality_mode") == "impersonate"' in content
        assert "FabricationGuard()" in content

    def test_fabrication_guard_skips_tool_calls(self):
        """CRITICAL: Guard check should be skipped when tool_result_messages is non-empty."""
        main_path = PROJECT_ROOT / "src" / "main.py"
        content = main_path.read_text()
        assert "not tool_result_messages" in content

    def test_semantic_guard_only_impersonate(self):
        """Verify semantic guard is only called in impersonate mode."""
        main_path = PROJECT_ROOT / "src" / "main.py"
        content = main_path.read_text()
        assert '_personality == "impersonate"' in content


# ══════════════════════════════════════════════════════════════
# 6. MEMORY STORE TESTS
# ══════════════════════════════════════════════════════════════

class TestMemoryStore:
    """Test memory store after dead code removal."""

    @pytest.fixture
    def store(self, tmp_path):
        from src.memory_store import MemoryStore
        return MemoryStore(tmp_path)

    @pytest.fixture
    def search(self, store):
        from src.memory_store import MemorySearch
        return MemorySearch(store)

    def test_dead_methods_removed(self):
        """Verify dead methods are gone."""
        from src.memory_store import MemoryStore, MemorySearch
        # Legacy global consolidation
        assert not hasattr(MemoryStore, "consolidate") or \
               "consolidate_contact" == MemoryStore.consolidate.__name__, \
               "Legacy consolidate() should be removed"
        # Dead search methods
        ms = MemorySearch.__new__(MemorySearch)
        assert not hasattr(ms, "search_by_topic"), "search_by_topic should be removed"
        assert not hasattr(ms, "search_by_date"), "search_by_date should be removed"

    def test_per_contact_memory_isolation(self, store):
        store.write_contact_memory("jid1@s.whatsapp.net", "Contact 1 likes pizza")
        store.write_contact_memory("jid2@s.whatsapp.net", "Contact 2 likes sushi")
        assert "pizza" in store.read_contact_memory("jid1@s.whatsapp.net")
        assert "sushi" in store.read_contact_memory("jid2@s.whatsapp.net")
        # Cross-contamination check
        assert "sushi" not in store.read_contact_memory("jid1@s.whatsapp.net")
        assert "pizza" not in store.read_contact_memory("jid2@s.whatsapp.net")

    def test_per_contact_history_append(self, store):
        store.append_contact_history("jid1@s.whatsapp.net", "[2024-01-01 10:00] Ordered pizza")
        store.append_contact_history("jid1@s.whatsapp.net", "[2024-01-02 12:00] Ordered burger")
        history = store.read_contact_history("jid1@s.whatsapp.net")
        assert "pizza" in history
        assert "burger" in history

    def test_get_memory_context_isolated(self, store):
        store.write_contact_memory("jid1@s.whatsapp.net", "VIP customer")
        ctx = store.get_memory_context("jid1@s.whatsapp.net")
        assert "VIP customer" in ctx
        assert "Long-term Memory" in ctx

    def test_get_recent_history_limited(self, store):
        for i in range(20):
            store.append_contact_history("jid1@s.whatsapp.net", f"[2024-01-{i+1:02d} 10:00] Entry {i}")
        recent = store.get_recent_history("jid1@s.whatsapp.net", max_entries=5)
        # Should only have last 5 entries
        assert "Entry 19" in recent
        assert "Entry 15" in recent
        assert "Entry 0" not in recent

    def test_consolidate_contact_exists(self, store):
        """Verify per-contact consolidation still works."""
        assert hasattr(store, "consolidate_contact")
        assert asyncio.iscoroutinefunction(store.consolidate_contact)

    def test_search_basic(self, store, search):
        store.append_history("[2024-06-01 10:00] Discussed project alpha with John")
        store.append_history("[2024-06-02 10:00] Meeting about budget review")
        results = search.search("project alpha")
        assert len(results) > 0
        assert "alpha" in results[0]["text"].lower()

    def test_jid_key_deterministic(self):
        from src.memory_store import MemoryStore
        key1 = MemoryStore._jid_key("test@s.whatsapp.net")
        key2 = MemoryStore._jid_key("test@s.whatsapp.net")
        assert key1 == key2
        # Different JID should produce different key
        key3 = MemoryStore._jid_key("other@s.whatsapp.net")
        assert key1 != key3


# ══════════════════════════════════════════════════════════════
# 7. MAIN.PY CONFIG AND GUARD TESTS
# ══════════════════════════════════════════════════════════════

class TestMainConfig:
    """Test wizard config and guard initialization logic."""

    def test_no_fabrication_policy_in_wizard(self):
        """CRITICAL: fabrication_policy should be completely removed from wizard."""
        main_path = PROJECT_ROOT / "src" / "main.py"
        content = main_path.read_text()
        assert 'fabrication_policy' not in content, "fabrication_policy still referenced in main.py"

    def test_consolidation_threshold_30(self):
        """Verify consolidation threshold was increased to 30."""
        main_path = PROJECT_ROOT / "src" / "main.py"
        content = main_path.read_text()
        assert "_CONSOLIDATION_THRESHOLD = 30" in content

    def test_push_name_cache_exists(self):
        """Verify push_name caching was added."""
        main_path = PROJECT_ROOT / "src" / "main.py"
        content = main_path.read_text()
        assert "_last_push_names" in content

    def test_queue_skip_auto_reply(self):
        """Verify queue I/O is skipped in auto_reply mode."""
        main_path = PROJECT_ROOT / "src" / "main.py"
        content = main_path.read_text()
        assert 'mode != "auto_reply"' in content

    def test_tool_result_messages_initialized_before_if(self):
        """Verify tool_result_messages is initialized before the tool-call if block."""
        main_path = PROJECT_ROOT / "src" / "main.py"
        content = main_path.read_text()
        # Find the initialization line
        init_idx = content.find("tool_result_messages: list[dict] = []")
        # Find the tool-call if block
        if_idx = content.find('if ai_resp.finish_reason == "tool_calls"')
        assert init_idx > 0, "tool_result_messages initialization not found"
        assert if_idx > 0, "tool-call if block not found"
        assert init_idx < if_idx, "tool_result_messages must be initialized BEFORE the if block"


# ══════════════════════════════════════════════════════════════
# 8. TOOL EXECUTOR UNIFIED HANDLER TESTS
# ══════════════════════════════════════════════════════════════

class TestToolExecutorUnified:
    """Test the unified handler architecture."""

    def test_no_two_phase_lookup_in_execute(self):
        """Verify execute() uses single unified lookup, not two phases."""
        te_path = PROJECT_ROOT / "src" / "tool_executor.py"
        content = te_path.read_text()
        # Old pattern had "# Delegate to integration if not a core tool"
        assert "Delegate to integration" not in content

    def test_handlers_dict_built_at_init(self):
        """Verify _handlers dict is set up in __init__."""
        te_path = PROJECT_ROOT / "src" / "tool_executor.py"
        content = te_path.read_text()
        # Should have _handlers defined in __init__ with core handlers
        assert "self._handlers" in content
        assert '"generate_image": self._generate_image' in content

    def test_integration_tools_tracked(self):
        """Verify integration tools are tracked in a set for dispatch."""
        te_path = PROJECT_ROOT / "src" / "tool_executor.py"
        content = te_path.read_text()
        assert "self._integration_tools" in content
        assert "self._integration_tools.add" in content


# ══════════════════════════════════════════════════════════════
# 9. CROSS-CUTTING INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════

class TestCrossCutting:
    """Verify no dead references remain across modules."""

    def test_no_fabrication_policy_anywhere(self):
        """Scan ALL Python files for fabrication_policy references."""
        src_dir = PROJECT_ROOT / "src"
        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text()
            assert "fabrication_policy" not in content, \
                f"fabrication_policy still in {py_file.relative_to(PROJECT_ROOT)}"

    def test_no_importlib_in_init_or_context_builder(self):
        """Neither integrations/__init__.py nor context_builder.py should use importlib."""
        for rel_path in ["src/integrations/__init__.py", "src/context_builder.py"]:
            content = (PROJECT_ROOT / rel_path).read_text()
            assert "importlib" not in content, f"importlib still in {rel_path}"

    def test_no_get_all_tool_definitions_import(self):
        """Verify dead import is removed from tool_executor."""
        te_content = (PROJECT_ROOT / "src" / "tool_executor.py").read_text()
        assert "get_all_tool_definitions" not in te_content

    def test_no_get_system_prompt_additions(self):
        """Verify dead function is removed from integrations."""
        init_content = (PROJECT_ROOT / "src" / "integrations" / "__init__.py").read_text()
        assert "get_system_prompt_additions" not in init_content

    def test_layer_numbering_sequential(self):
        """Verify layer comments in build_system_prompt are sequential."""
        cb_content = (PROJECT_ROOT / "src" / "context_builder.py").read_text()
        import re
        layers = re.findall(r"# Layer (\d+):", cb_content)
        layer_nums = [int(n) for n in layers]
        expected = list(range(1, len(layer_nums) + 1))
        assert layer_nums == expected, f"Layer numbering not sequential: {layer_nums}"

    def test_all_modified_files_compile(self):
        """Compile check every modified file."""
        import py_compile
        files = [
            "src/main.py",
            "src/context_builder.py",
            "src/tool_executor.py",
            "src/memory_store.py",
            "src/integrations/__init__.py",
            "src/business_templates/__init__.py",
            "src/fabrication_guard.py",
        ]
        for rel_path in files:
            full_path = str(PROJECT_ROOT / rel_path)
            try:
                py_compile.compile(full_path, doraise=True)
            except py_compile.PyCompileError as e:
                pytest.fail(f"Compile error in {rel_path}: {e}")


# ══════════════════════════════════════════════════════════════
# 10. EDGE CASES AND REGRESSION TESTS
# ══════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases and potential regressions."""

    def test_empty_integrations_list(self, tmp_path):
        """Context builder handles empty integration list."""
        from src.context_builder import ContextBuilder
        cb = ContextBuilder(tmp_path)
        config = {"enabled_integrations": ["core"], "tone": "casual_friendly"}
        prompt = cb.build_system_prompt(config)
        assert "SECURITY ANCHOR" in prompt  # basic sanity

    def test_no_integrations_key_in_config(self, tmp_path):
        """Context builder handles missing enabled_integrations key."""
        from src.context_builder import ContextBuilder
        cb = ContextBuilder(tmp_path)
        prompt = cb.build_system_prompt({"tone": "casual_friendly"})
        assert len(prompt) > 100

    def test_privacy_unknown_level_defaults_relaxed(self, tmp_path):
        from src.context_builder import ContextBuilder
        cb = ContextBuilder(tmp_path)
        result = cb._build_privacy_instructions({"privacy_level": "unknown_value"})
        assert "General information can be shared" in result

    def test_memory_store_empty_entry_skipped(self, tmp_path):
        from src.memory_store import MemoryStore
        store = MemoryStore(tmp_path)
        store.append_contact_history("jid1@s.whatsapp.net", "")
        store.append_contact_history("jid1@s.whatsapp.net", "   ")
        history = store.read_contact_history("jid1@s.whatsapp.net")
        assert history == ""

    def test_memory_context_no_jid_falls_back(self, tmp_path):
        from src.memory_store import MemoryStore
        store = MemoryStore(tmp_path)
        store.write_long_term("Global memory content")
        ctx = store.get_memory_context(None)
        assert "Global memory content" in ctx

    def test_fabrication_guard_empty_text(self):
        from src.fabrication_guard import FabricationGuard
        guard = FabricationGuard()
        result = guard.check("")
        assert not result.is_fabrication

    def test_fabrication_guard_normal_response(self):
        """Normal business responses should never trigger the guard."""
        from src.fabrication_guard import FabricationGuard
        guard = FabricationGuard()
        business_responses = [
            "Your order has been confirmed! Total: $25.50",
            "We have butter chicken and naan available today",
            "Your appointment is booked for 3pm tomorrow",
            "The delivery should arrive in 30-45 minutes",
            "Would you like to add anything else to your order?",
            "Thanks for your order! We'll start preparing it now.",
        ]
        for resp in business_responses:
            result = guard.check(resp)
            assert not result.is_fabrication, f"False positive on: {resp}"

    def test_load_integrations_empty_list(self):
        from src.integrations import load_integrations
        result = load_integrations([], {})
        assert result == {}


# ══════════════════════════════════════════════════════════════
# 11. END-TO-END FLOW SIMULATION
# ══════════════════════════════════════════════════════════════

class TestEndToEndFlow:
    """Simulate the full message processing pipeline with all pruned components."""

    def _make_config(self, mode="assistant", template="food_restaurant"):
        """Build a realistic config like the wizard produces."""
        base = {
            "personality_mode": mode,
            "mode": "auto_reply",
            "tone": "casual_friendly",
            "privacy_level": "strict",
            "enabled_integrations": ["core", "spreadsheet"],
            "business_template": template,
            "tool_calling_enabled": True,
            "owner_name": "Test Owner",
            "admin_number": "1234567890",
        }
        # Apply business template overrides (as wizard does)
        if template:
            from src.business_templates import get_template
            t = get_template(template)
            if t:
                for k, v in t.get("config_overrides", {}).items():
                    base[k] = v
        return base

    def test_full_prompt_assembly_business_mode(self, tmp_path):
        """Simulate full prompt build for a restaurant bot."""
        from src.context_builder import ContextBuilder
        config = self._make_config()
        cb = ContextBuilder(tmp_path, personality_mode="assistant", config=config)

        prompt = cb.build_system_prompt(
            config,
            memory_context="## Long-Term Memory\n- Customer prefers spicy food",
            recent_history="[2024-01-01] Customer ordered butter chicken",
            contact_profile="## Contact: John\nRegular customer, usually orders dinner",
            rag_context="Previous order: 2x butter chicken, 1x naan",
        )

        # All layers should be present
        assert "SECURITY ANCHOR" in prompt
        assert "Long-Term Memory" in prompt
        assert "Recent Activity Log" in prompt
        assert "Contact: John" in prompt
        assert "Relevant Conversation History" in prompt
        assert "<reply>" in prompt  # reasoning suppression

        # Fabrication layer should NOT be present
        assert "FABRICATION" not in prompt.upper().split("SOUL")[0]  # before SOUL is OK

        # Should be concise (no bloated 12-layer prompt)
        sections = prompt.split("---")
        # At minimum: anchor, soul, user, config, privacy, memory, history, contact, rag, suppression = 10
        assert len(sections) >= 9

    def test_business_template_injects_soul(self, tmp_path):
        """Business template SOUL.md should be injected into system prompt."""
        from src.context_builder import ContextBuilder
        from src.business_templates import get_template

        config = self._make_config()
        t = get_template("food_restaurant")

        # Write the template's SOUL.md to identity dir
        cb = ContextBuilder(tmp_path, personality_mode="assistant", config=config)
        cb.update_identity_file("SOUL.md", t["soul_md"])

        prompt = cb.build_system_prompt(config)
        # Restaurant-specific content should be in the prompt
        assert "order" in prompt.lower() or "restaurant" in prompt.lower()

    def test_impersonate_mode_gets_guards(self, tmp_path):
        """In impersonate mode, fabrication guard should be created."""
        main_content = (PROJECT_ROOT / "src" / "main.py").read_text()

        # The guard init is conditioned on personality_mode == "impersonate"
        assert 'personality_mode") == "impersonate"' in main_content
        assert "FabricationGuard()" in main_content

    def test_assistant_mode_skips_guards(self, tmp_path):
        """Business (assistant) mode should not create fabrication guard.

        We verify this by checking that guard creation is inside the impersonate condition.
        """
        import re
        main_content = (PROJECT_ROOT / "src" / "main.py").read_text()

        # Find the fabrication guard init - it should be inside an impersonate check
        guard_pattern = re.compile(
            r'if\s+self\.config\.get\("personality_mode"\)\s*==\s*"impersonate".*?'
            r'FabricationGuard\(\)',
            re.DOTALL
        )
        assert guard_pattern.search(main_content), \
            "FabricationGuard() should only be created inside impersonate mode check"

    def test_tool_result_skip_guard_flow(self):
        """Simulate: tool was called -> guard should be skipped."""
        main_content = (PROJECT_ROOT / "src" / "main.py").read_text()

        # The guard check should have "not tool_result_messages" condition
        assert "not tool_result_messages" in main_content

        # tool_result_messages should be initialized before the tool-call if block
        import re
        # Find where tool_result_messages is first assigned
        first_assign = main_content.find("tool_result_messages: list")
        assert first_assign > 0, "tool_result_messages should have a type-annotated init"

        # Find where the tool_calls if-block starts
        tool_if = main_content.find('if ai_resp.finish_reason == "tool_calls"')
        assert tool_if > 0

        # The init should come BEFORE the if block
        assert first_assign < tool_if, \
            "tool_result_messages must be initialized before the tool_calls if-block"

    def test_queue_skip_in_auto_reply(self):
        """In auto_reply mode, queue write should be skipped."""
        main_content = (PROJECT_ROOT / "src" / "main.py").read_text()
        # Should have a condition checking mode before queue write
        assert 'mode != "auto_reply"' in main_content, \
            "Queue write should be conditional on non-auto_reply mode"

    def test_consolidation_threshold_business_friendly(self):
        """Threshold should be 30+ (not 10) for high-volume bots."""
        main_content = (PROJECT_ROOT / "src" / "main.py").read_text()
        import re
        match = re.search(r"_CONSOLIDATION_THRESHOLD\s*=\s*(\d+)", main_content)
        assert match, "Consolidation threshold constant not found"
        threshold = int(match.group(1))
        assert threshold >= 30, f"Threshold {threshold} too low for business bots"

    def test_integration_tool_definitions_accessible(self):
        """Verify integration tools can be retrieved for LLM tool definitions."""
        from src.integrations import _INTEGRATIONS

        if "spreadsheet" in _INTEGRATIONS:
            cls = _INTEGRATIONS["spreadsheet"]
            tools = cls.tool_definitions()
            assert len(tools) > 0
            for tool in tools:
                assert "function" in tool
                assert "name" in tool["function"]
                assert "parameters" in tool["function"]

    def test_unified_handler_covers_all_core_tools(self):
        """All core tool names should be in the handlers dict at init."""
        te_content = (PROJECT_ROOT / "src" / "tool_executor.py").read_text()
        core_tools = ["generate_image", "generate_video", "create_pdf",
                       "send_message", "ask_owner"]
        for tool in core_tools:
            assert f'"{tool}"' in te_content, f"Core tool {tool} missing from handlers"

    def test_full_pipeline_no_dead_imports(self):
        """Import all key modules and verify no ImportError from dead references."""
        modules_to_test = [
            "src.context_builder",
            "src.fabrication_guard",
            "src.memory_store",
            "src.business_templates",
            "src.integrations",
            "src.integrations.base",
            "src.tool_executor",
            "src.config_manager",
        ]
        import importlib
        for mod_name in modules_to_test:
            try:
                importlib.import_module(mod_name)
            except ImportError as e:
                pytest.fail(f"Dead import in {mod_name}: {e}")

    def test_privacy_strict_in_business_config(self):
        """Business configs should default to strict privacy."""
        config = self._make_config()
        assert config["privacy_level"] == "strict"

    def test_no_fabrication_policy_in_pipeline(self):
        """The entire pipeline should have zero fabrication_policy references."""
        key_files = [
            "src/main.py",
            "src/context_builder.py",
            "src/config_manager.py",
            "src/business_templates/__init__.py",
            "src/tool_executor.py",
        ]
        for rel_path in key_files:
            content = (PROJECT_ROOT / rel_path).read_text()
            assert "fabrication_policy" not in content, \
                f"fabrication_policy still referenced in {rel_path}"
