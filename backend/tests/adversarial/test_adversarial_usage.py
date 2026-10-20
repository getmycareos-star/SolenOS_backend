"""
Tests demonstrating the usage of Adversarial Test Generators.
Shows how to generate test cases for security, boundary testing, and error conditions.
"""

import pytest
from backend.tests.adversarial.adversarial_generators import (
    AdversarialTestGenerator,
    get_malformed_ids,
    get_extreme_values,
    get_prohibited_content,
    get_oversized_inputs,
    get_unicode_edge_cases,
    get_temporal_adversarial,
    get_resource_exhaustion_cases,
    get_special_numeric_values,
    get_all_adversarial_categories
)


def test_adversarial_generator_creation():
    """Test that we can create an adversarial test generator."""
    generator = AdversarialTestGenerator()
    assert generator is not None
    
    # Test with seed for reproducibility
    generator_seeded = AdversarialTestGenerator(seed=42)
    assert generator_seeded is not None


def test_get_malformed_ids():
    """Test getting malformed ID test cases."""
    malformed_ids = get_malformed_ids()
    assert isinstance(malformed_ids, list)
    assert len(malformed_ids) > 0
    
    # Check that we have the expected malicious patterns
    id_values = [item["id"] for item in malformed_ids]
    assert "" in id_values  # Empty string
    assert " " in id_values  # Whitespace only
    assert len("a" * 10000) in [len(x) for x in id_values]  # Very long string
    
    # Check for security-relevant patterns
    security_patterns = ["<script>", "DROP TABLE", "javascript:", "$(ls)", "`ls`"]
    found_patterns = []
    for item in malformed_ids:
        for pattern in security_patterns:
            if pattern in item["id"]:
                found_patterns.append(pattern)
                break
    
    assert len(found_patterns) > 0  # Should find some security patterns


def test_get_extreme_values():
    """Test getting extreme value test cases."""
    extreme_values = get_extreme_values()
    assert isinstance(extreme_values, list)
    assert len(extreme_values) > 0
    
    # Check for specific extreme values
    values = [item["value"] for item in extreme_values]
    assert -1 in values  # Negative one
    assert 0 in values   # Zero
    assert 1 in values   # One
    assert float('inf') in values  # Positive infinity
    assert float('-inf') in values  # Negative infinity
    # Note: NaN comparison is special, so we'll check differently
    nan_values = [item for item in extreme_values if str(item["value"]) == "nan"]
    assert len(nan_values) > 0  # Should have NaN values


def test_get_prohibited_content():
    """Test getting prohibited content test cases."""
    prohibited_content = get_prohibited_content()
    assert isinstance(prohibited_content, list)
    assert len(prohibited_content) > 0
    
    # Check that we have security-relevant prohibited content
    texts = [item["text"] for item in prohibited_content]
    security_indicators = ["<script>", "javascript:", "onload=", "eval(", "document.", "| ls", "; ls"]
    found_indicators = []
    for text in texts:
        for indicator in security_indicators:
            if indicator in text:
                found_indicators.append(indicator)
                break
    
    assert len(found_indicators) > 0  # Should find security indicators


def test_get_oversized_inputs():
    """Test getting oversized input test cases."""
    oversized_inputs = get_oversized_inputs()
    assert isinstance(oversized_inputs, list)
    assert len(oversized_inputs) > 0
    
    # Check that we have various size categories
    has_kb = any("1KB" in item.get("description", "") for item in oversized_inputs)
    has_mb = any("1MB" in item.get("description", "") for item in oversized_inputs)
    assert has_kb or has_mb  # Should have some size-descriptive items
    
    # Check for actually large inputs
    large_text_found = False
    large_list_found = False
    for item in oversized_inputs:
        if "text" in item and isinstance(item["text"], str) and len(item["text"]) >= 1000:
            large_text_found = True
        if "list" in item and isinstance(item["list"], list) and len(item["list"]) >= 1000:
            large_list_found = True
    
    assert large_text_found or large_list_found  # Should have some large inputs


def test_get_unicode_edge_cases():
    """Test getting Unicode edge case test cases."""
    unicode_cases = get_unicode_edge_cases()
    assert isinstance(unicode_cases, list)
    assert len(unicode_cases) > 0
    
    # Check for various Unicode categories
    texts = [item["text"] for item in unicode_cases]
    
    # Emojis
    has_emoji = any("🙂" in text or "☃" in text or any(ord(char) > 127 for char in text) for text in texts)
    # Accented characters
    has_accented = any("é" in text or "ï" in text or "ñ" in text for text in texts)
    # Non-Latin scripts
    has_non_latin = any(any(ord(char) > 127 for char in text) and 
                       any(c in text for c in "москва北京こんにちは안녕하세요مرحبا") for text in texts)
    
    assert has_emoji or has_accented or has_non_latin  # Should have some Unicode variety


def test_get_temporal_adversarial():
    """Test getting temporal adversarial test cases."""
    temporal_cases = get_temporal_adversarial()
    assert isinstance(temporal_cases, list)
    assert len(temporal_cases) > 0
    
    # Check for various temporal edge cases
    timestamps = [item["timestamp"] for item in temporal_cases]
    
    # Invalid dates
    has_invalid_date = any("2023-02-30" in ts or "2023-13-01" in ts or "not-a-date" in ts 
                          for ts in timestamps)
    # Boundary dates
    has_boundary = any("9999-12-31" in ts or "0001-01-01" in ts for ts in timestamps)
    # Empty/malformed
    has_empty = any(ts == "" for ts in timestamps)
    
    assert has_invalid_date or has_boundary or has_empty  # Should have temporal adversarial cases


def test_get_resource_exhaustion_cases():
    """Test getting resource exhaustion test cases."""
    exhaustion_cases = get_resource_exhaustion_cases()
    assert isinstance(exhaustion_cases, list)
    assert len(exhaustion_cases) > 0
    
    # Check for various resource exhaustion types
    cases = [item.get("description", "") for item in exhaustion_cases]
    
    has_deep = any("Deep nesting" in desc for desc in cases)
    has_wide = any("Wide structure" in desc for desc in cases)
    has_large_count = any("Large collection" in desc or "Very large collection" in desc for desc in cases)
    
    assert has_deep or has_wide or has_large_count  # Should have resource exhaustion cases


def test_get_special_numeric_values():
    """Test getting special numeric value test cases."""
    special_values = get_special_numeric_values()
    assert isinstance(special_values, list)
    assert len(special_values) > 0
    
    # Check for special numeric values
    values = [item["value"] for item in special_values]
    
    has_zero = 0 in values
    has_inf = float('inf') in values
    has_neginf = float('-inf') in values
    # NaN is tricky to test directly
    has_nan = any(str(v) == "nan" for v in values)
    has_large = any(abs(v) > 1e100 for v in values if isinstance(v, (int, float)) and not (v != v))  # Exclude NaN
    
    assert has_zero or has_inf or has_neginf or has_nan or has_large  # Should have special values


def test_get_all_adversarial_categories():
    """Test getting all adversarial categories at once."""
    all_categories = get_all_adversarial_categories()
    assert isinstance(all_categories, dict)
    assert len(all_categories) > 0
    
    expected_categories = [
        "malformed_ids", "extreme_values", "prohibited_content", 
        "oversized_inputs", "unicode_edge_cases", "temporal_adversarial",
        "resource_exhaustion", "special_numeric_values"
    ]
    
    for category in expected_categories:
        assert category in all_categories
        assert isinstance(all_categories[category], list)
        assert len(all_categories[category]) > 0


def test_adversarial_generator_with_seed():
    """Test that adversarial generators are reproducible with seeds."""
    gen1 = AdversarialTestGenerator(seed=12345)
    gen2 = AdversarialTestGenerator(seed=12345)
    
    # Generate the same type of data from both
    ids1 = gen1.generate_malformed_ids()
    ids2 = gen2.generate_malformed_ids()
    
    # Should produce the same results
    assert len(ids1) == len(ids2)
    if ids1 and ids2:
        assert ids1[0]["id"] == ids2[0]["id"]


def test_adversarial_covers_security_concerns():
    """Test that adversarial generators cover common security concerns."""
    all_cats = get_all_adversarial_categories()
    
    # Injection attacks
    injection_found = False
    for item in all_cats.get("malformed_ids", []):
        if any(pattern in item["id"] for pattern in 
               ["<script>", "javascript:", "DROP TABLE", "$(ls)", "`ls*", "|", ";", "&"]):
            injection_found = True
            break
    
    # Buffer overflow / resource exhaustion
    resource_found = any(len(cats.get("oversized_inputs", [])) > 0 for cats in [all_cats])
    
    # Unicode issues
    unicode_found = len(all_cats.get("unicode_edge_cases", [])) > 0
    
    # Temporal issues
    temporal_found = len(all_cats.get("temporal_adversarial", [])) > 0
    
    assert injection_found or resource_found or unicode_found or temporal_found


def test_adversarial_generators_are_deterministic():
    """Test that generators with same seed produce same output."""
    generator1 = AdversarialTestGenerator(seed=999)
    generator2 = AdversarialTestGenerator(seed=999)
    
    # Test multiple generator methods
    methods_to_test = [
        "generate_malformed_ids",
        "generate_extreme_values", 
        "generate_prohibited_content",
        "generate_unicode_edge_cases"
    ]
    
    for method_name in methods_to_test:
        method1 = getattr(generator1, method_name)
        method2 = getattr(generator2, method_name)
        
        result1 = method1()
        result2 = method2()
        
        assert len(result1) == len(result2)
        if result1 and result2:
            # Compare first items - they should be identical with same seed
            assert result1[0] == result2[0]


if __name__ == "__main__":
    # Run the tests
    test_adversarial_generator_creation()
    test_get_malformed_ids()
    test_get_extreme_values()
    test_get_prohibited_content()
    test_get_oversized_inputs()
    test_get_unicode_edge_cases()
    test_get_temporal_adversarial()
    test_get_resource_exhaustion_cases()
    test_get_special_numeric_values()
    test_get_all_adversarial_categories()
    test_adversarial_generator_with_seed()
    test_adversarial_covers_security_concerns()
    test_adversarial_generators_are_deterministic()
    print("All adversarial tests passed!")