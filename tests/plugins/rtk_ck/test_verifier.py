"""Tests for RTK-VF Response Verifier — claim extraction + falsification."""
from __future__ import annotations

import pytest


class TestClaimExtractor:
    def test_empty_response_no_claims(self):
        from plugins.rtk_ck.verifier import ClaimExtractor
        assert ClaimExtractor.extract("") == []

    def test_no_factual_content_no_claims(self):
        from plugins.rtk_ck.verifier import ClaimExtractor
        assert ClaimExtractor.extract("Hello! How can I help you today?") == []

    def test_contains_claim(self):
        from plugins.rtk_ck.verifier import ClaimExtractor
        response = "The file contains 127.0.0.1 localhost."
        claims = ClaimExtractor.extract(response)
        assert len(claims) >= 1
        assert any("127.0.0.1" in c for c in claims)

    def test_has_claim(self):
        from plugins.rtk_ck.verifier import ClaimExtractor
        response = "There are 42 tests in the project."
        claims = ClaimExtractor.extract(response)
        assert len(claims) >= 1
        assert any("42" in c for c in claims)

    def test_multiple_claims(self):
        from plugins.rtk_ck.verifier import ClaimExtractor
        response = "The config has 3 settings. There are 42 tests."
        claims = ClaimExtractor.extract(response)
        assert len(claims) >= 2


class TestClaimFalsifier:
    def test_no_tool_results_no_flags(self):
        from plugins.rtk_ck.verifier import ClaimFalsifier
        assert ClaimFalsifier.falsify(["test"], []) == []

    def test_matching_claim_no_flag(self):
        from plugins.rtk_ck.verifier import ClaimFalsifier
        flags = ClaimFalsifier.falsify(
            claims=["127.0.0.1 localhost"],
            tool_results=[{"name": "read_file", "content": "127.0.0.1 localhost"}],
        )
        assert len(flags) == 0

    def test_ip_contradiction_flagged(self):
        from plugins.rtk_ck.verifier import ClaimFalsifier
        flags = ClaimFalsifier.falsify(
            claims=["192.168.1.1 gateway"],
            tool_results=[{"name": "read_file", "content": "127.0.0.1 localhost"}],
        )
        assert len(flags) >= 1

    def test_unverifiable_claim_flagged(self):
        from plugins.rtk_ck.verifier import ClaimFalsifier
        flags = ClaimFalsifier.falsify(
            claims=["42 tables"],
            tool_results=[{"name": "read_file", "content": "127.0.0.1 localhost"}],
        )
        assert len(flags) >= 1

    def test_multiple_claims_mixed(self):
        from plugins.rtk_ck.verifier import ClaimFalsifier
        flags = ClaimFalsifier.falsify(
            claims=["127.0.0.1 localhost", "99 settings"],
            tool_results=[
                {"name": "read_file", "content": "127.0.0.1 localhost"},
                {"name": "read_file", "content": "setting_a=1"},
            ],
        )
        assert len(flags) >= 1


class TestResponseVerifier:
    def test_clean_response_unchanged(self):
        from plugins.rtk_ck.verifier import ResponseVerifier
        result = ResponseVerifier.verify("Hello! How can I help?", [])
        assert result == "Hello! How can I help?"

    def test_verified_response_unchanged(self):
        from plugins.rtk_ck.verifier import ResponseVerifier
        result = ResponseVerifier.verify(
            "The file contains 127.0.0.1 localhost.",
            [{"name": "read_file", "content": "127.0.0.1 localhost"}],
        )
        assert result == "The file contains 127.0.0.1 localhost."

    def test_flagged_response_gets_correction(self):
        from plugins.rtk_ck.verifier import ResponseVerifier
        response = "The configuration file contains 192.168.1.1 as the main gateway address for all network traffic."
        result = ResponseVerifier.verify(
            response,
            [{"name": "read_file", "content": "127.0.0.1 localhost"}],
        )
        assert result != response
        assert "⚠️" in result or "verification" in result.lower()
        assert response in result

    def test_short_response_skipped(self):
        from plugins.rtk_ck.verifier import ResponseVerifier
        assert ResponseVerifier.verify("Done!", [{"name": "read_file", "content": "x"}]) == "Done!"


class TestVerifierHook:
    def test_post_llm_call_with_verification(self):
        from plugins.rtk_ck.verifier import verifier_post_llm_call
        response = "The configuration file contains 192.168.1.1 as the main gateway address for all network traffic."
        result = verifier_post_llm_call(
            session_id="s1",
            response=response,
            tool_results=[{"name": "read_file", "content": "127.0.0.1 localhost"}],
        )
        assert result != response

    def test_post_llm_call_no_tool_results(self):
        from plugins.rtk_ck.verifier import verifier_post_llm_call
        assert verifier_post_llm_call(session_id="s1", response="Hello!") == "Hello!"

    def test_post_llm_call_clean_response(self):
        from plugins.rtk_ck.verifier import verifier_post_llm_call
        result = verifier_post_llm_call(
            session_id="s1",
            response="The file contains 127.0.0.1 localhost.",
            tool_results=[{"name": "read_file", "content": "127.0.0.1 localhost"}],
        )
        assert result == "The file contains 127.0.0.1 localhost."