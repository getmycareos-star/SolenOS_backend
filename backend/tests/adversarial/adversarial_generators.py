"""
Adversarial test generators for SolenOS.
Creates inputs designed to test system boundaries, error handling, and security.
"""

import random
import string
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta


class AdversarialTestGenerator:
    """Generator for adversarial test cases."""
    
    def __init__(self, seed: Optional[int] = None):
        """Initialize the adversarial test generator.
        
        Args:
            seed: Optional seed for reproducible test generation
        """
        if seed is not None:
            random.seed(seed)
    
    def generate_malformed_ids(self) -> List[Dict[str, Any]]:
        """Generate various malformed ID formats to test validation."""
        malicious_ids = [
            "",                           # Empty string
            " ",                          # Whitespace only
            "a" * 10000,                  # Extremely long string
            "a\nb",                       # Contains newline
            "a\tb",                       # Contains tab
            "a\rb",                       # Contains carriage return
            "a\x00b",                     # Contains null byte
            "a�b",                        # Contains invalid Unicode
            "../../etc/passwd",           # Path traversal attempt
            "<script>alert('xss')</script>", # XSS attempt
            "'; DROP TABLE users; --",    # SQL injection attempt
            "|| cat /etc/passwd",         # Command injection attempt
            "${7*7}",                     # Template injection attempt
            "{{7*7}}",                    # Another template injection
            "%{7*7}",                     # Yet another template injection
        ]
        
        return [{"id": test_id, "description": f"Malformed ID: {repr(test_id)[:50]}..."} 
                for test_id in malicious_ids]
    
    def generate_extreme_values(self) -> List[Dict[str, Any]]:
        """Generate extreme values for numeric fields."""
        extreme_values = [
            {"value": -1, "description": "Negative one"},
            {"value": 0, "description": "Zero"},
            {"value": 1, "description": "One"},
            {"value": 2**31 - 1, "description": "Max 32-bit signed int"},
            {"value": 2**31, "description": "Min 32-bit signed int overflow"},
            {"value": 2**63 - 1, "description": "Max 64-bit signed int"},
            {"value": float('inf'), "description": "Positive infinity"},
            {"value": float('-inf'), "description": "Negative infinity"},
            {"value": float('nan'), "description": "NaN (Not a Number)"},
            {"value": 0.0, "description": "Zero float"},
            {"value": -0.0, "description": "Negative zero float"},
            {"value": 1.0, "description": "One float"},
            {"value": 1.7976931348623157e+308, "description": "Max float64"},
            {"value": 2.2250738585072014e-308, "description": "Min positive float64"},
            {"value": 1e-324, "description": "Min positive subnormal float64"},
        ]
        
        return extreme_values
    
    def generate_prohibited_content(self) -> List[Dict[str, Any]]:
        """Generate content that should be rejected or sanitized."""
        prohibited_content = [
            {"text": "<script>", "description": "Opening script tag"},
            {"text": "</script>", "description": "Closing script tag"},
            {"text": "javascript:", "description": "JavaScript protocol"},
            {"text": "data:", "description": "Data protocol"},
            {"text": "vbscript:", "description": "VBScript protocol"},
            {"text": "onload=", "description": "Event handler attribute"},
            {"text": "onerror=", "description": "Event handler attribute"},
            {"text": "onclick=", "description": "Event handler attribute"},
            {"text": "alert(", "description": "Alert function call"},
            {"text": "eval(", "description": "Eval function call"},
            {"text": "document.", "description": "Document object access"},
            {"text": "<img src=x onerror=alert(1)>", "description": "Classic XSS vector"},
            {"text": "'>\"><img src=x onerror=alert(1)>", "description": "Breaking out of quotes XSS"},
            {"text": "<?php echo system('ls'); ?>", "description": "PHP code injection"},
            {"text": "<%= system('ls') %>", "description": "ERB code injection"},
            {"text": "${pwnd}", "description": "Expression language injection"},
            {"text": "| ls", "description": "Pipe command"},
            {"text": "; ls", "description": "Semicolon command"},
            {"text": "& ls", "description": "Ampersand command"},
            {"text": "&& ls", "description": "Double ampersand command"},
            {"text": "|| ls", "description": "Double pipe command"},
            {"text": "$(ls)", "description": "Command substitution"},
            {"text": "`ls`", "description": "Backtick command substitution"},
        ]
        
        return prohibited_content
    
    def generate_oversized_inputs(self) -> List[Dict[str, Any]]:
        """Generate inputs that exceed reasonable size limits."""
        oversized_inputs = [
            {"text": "x" * 1000, "description": "1KB string"},
            {"text": "x" * 10000, "description": "10KB string"},
            {"text": "x" * 100000, "description": "100KB string"},
            {"text": "x" * 1000000, "description": "1MB string"},
            {"text": "x" * 10000000, "description": "10MB string"},
            {"list": list(range(1000)), "description": "List with 1000 items"},
            {"list": list(range(10000)), "description": "List with 10000 items"},
            {"list": list(range(100000)), "description": "List with 100000 items"},
        ]
        
        return oversized_inputs
    
    def generate_unicode_edge_cases(self) -> List[Dict[str, Any]]:
        """Generate Unicode edge cases that might cause issues."""
        unicode_cases = [
            {"text": "🙂", "description": "Simple emoji"},
            {"text": "🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂", "description": "Multiple emojis"},
            {"text": "\u0000", "description": "Null character"},
            {"text": "\u0001\u0002\u0003", "description": "Control characters"},
            {"text": "\ufffd", "description": "Replacement character"},
            {"text": "\U0001f600", "description": "Emoji in UTF-32 notation"},
            {"text": "café", "description": "Latin accented character"},
            {"text": "naïve", "description": "Latin accented character"},
            {"text": " résumé ", "description": "Text with spaces and accents"},
            {"text": "москва", "description": "Cyrillic text"},
            {"text": "北京", "description": "Chinese characters"},
            {"text": "こんにちは", "description": "Japanese text"},
            {"text": "안녕하세요", "description": "Korean text"},
            {"text": "مرحبا", "description": "Arabic text (RTL)"},
            {"text": "☃", "description": "Snowman Unicode character"},
            {"text": "∑∏∫√∞≠≤≥±÷×", "description": "Mathematical operators"},
            {"text": "♠♥♦♣", "description": "Playing card suits"},
            {"text": "☆★☾☽☀☁☂☃⚡", "description": "Various symbols"},
        ]
        
        return unicode_cases
    
    def generate_temporal_adversarial(self) -> List[Dict[str, Any]]:
        """Generate temporally adversarial inputs."""
        now = datetime.now()
        
        temporal_cases = [
            {"timestamp": (now - timedelta(days=365*100)).isoformat(), 
             "description": "Date far in the past (100 years)"},
            {"timestamp": (now + timedelta(days=365*100)).isoformat(), 
             "description": "Date far in the future (100 years)"},
            {"timestamp": "9999-12-31T23:59:59Z", 
             "description": "Maximum reasonable date"},
            {"timestamp": "0001-01-01T00:00:00Z", 
             "description": "Minimum reasonable date"},
            {"timestamp": "2023-02-30T12:00:00Z", 
             "description": "Invalid date (February 30)"},
            {"timestamp": "2023-13-01T12:00:00Z", 
             "description": "Invalid month (month 13)"},
            {"timestamp": "2023-00-01T12:00:00Z", 
             "description": "Invalid month (month 0)"},
            {"timestamp": "2023-04-31T12:00:00Z", 
             "description": "Invalid date (April 31)"},
            {"timestamp": "not-a-date", 
             "description": "Completely invalid date string"},
            {"timestamp": "", 
             "description": "Empty timestamp"},
            {"timestamp": "2023-04-15T25:00:00Z", 
             "description": "Invalid hour (25)"},
            {"timestamp": "2023-04-15T12:60:00Z", 
             "description": "Invalid minute (60)"},
            {"timestamp": "2023-04-15T12:30:60Z", 
             "description": "Invalid second (60)"},
        ]
        
        return temporal_cases
    
    def generate_resource_exhaustion_cases(self) -> List[Dict[str, Any]]:
        """Generate inputs designed to exhaust system resources."""
        exhaustion_cases = [
            {"depth": 1000, "description": "Deep nesting level 1000"},
            {"depth": 10000, "description": "Deep nesting level 10000"},
            {"width": 1000, "description": "Wide structure with 1000 elements"},
            {"width": 10000, "description": "Wide structure with 10000 elements"},
            {"count": 100000, "description": "Large collection of 100000 items"},
            {"count": 1000000, "description": "Very large collection of 1000000 items"},
        ]
        
        return exhaustion_cases
    
    def generate_special_numeric_values(self) -> List[Dict[str, Any]]:
        """Generate special numeric values that might cause issues."""
        special_values = [
            {"value": 0, "description": "Zero"},
            {"value": 1, "description": "One"},
            {"value": -1, "description": "Negative one"},
            {"value": 2**31 - 1, "description": "Max int32"},
            {"value": -2**31, "description": "Min int32"},
            {"value": 2**63 - 1, "description": "Max int64"},
            {"value": -2**63, "description": "Min int64"},
            {"value": sys.maxsize, "description": "Platform max size"},
            {"value": -sys.maxsize - 1, "description": "Platform min size"},
            {"value": float('inf'), "description": "Positive infinity"},
            {"value": float('-inf'), "description": "Negative infinity"},
            {"value": float('nan'), "description": "NaN"},
            {"value": 0.0, "description": "Positive zero"},
            {"value": -0.0, "description": "Negative zero"},
            {"value": 1.0, "description": "One point zero"},
            {"value": -1.0, "description": "Negative one point zero"},
            {"value": 1e100, "description": "Large positive float"},
            {"value": -1e100, "description": "Large negative float"},
            {"value": 1e-100, "description": "Small positive float"},
            {"value": -1e-100, "description": "Small negative float"},
            {"value": 1.7976931348623157e+308, "description": "Max float64"},
            {"value": 2.2250738585072014e-308, "description": "Min positive normal float64"},
            {"value": 5e-324, "description": "Min positive subnormal float64"},
        ]
        
        return special_values


# Convenience functions for easy access to common adversarial patterns
def get_malformed_ids() -> List[Dict[str, Any]]:
    """Get list of malformed ID test cases."""
    generator = AdversarialTestGenerator()
    return generator.generate_malformed_ids()


def get_extreme_values() -> List[Dict[str, Any]]:
    """Get list of extreme value test cases."""
    generator = AdversarialTestGenerator()
    return generator.generate_extreme_values()


def get_prohibited_content() -> List[Dict[str, Any]]:
    """Get list of prohibited content test cases."""
    generator = AdversarialTestGenerator()
    return generator.generate_prohibited_content()


def get_oversized_inputs() -> List[Dict[str, Any]]:
    """Get list of oversized input test cases."""
    generator = AdversarialTestGenerator()
    return generator.generate_oversized_inputs()


def get_unicode_edge_cases() -> List[Dict[str, Any]]:
    """Get list of Unicode edge case test cases."""
    generator = AdversarialTestGenerator()
    return generator.generate_unicode_edge_cases()


def get_temporal_adversarial() -> List[Dict[str, Any]]:
    """Get list of temporal adversarial test cases."""
    generator = AdversarialTestGenerator()
    return generator.generate_temporal_adversarial()


def get_resource_exhaustion_cases() -> List[Dict[str, Any]]:
    """Get list of resource exhaustion test cases."""
    generator = AdversarialTestGenerator()
    return generator.generate_resource_exhaustion_cases()


def get_special_numeric_values() -> List[Dict[str, Any]]:
    """Get list of special numeric value test cases."""
    generator = AdversarialTestGenerator()
    return generator.generate_special_numeric_values()


def get_all_adversarial_categories() -> Dict[str, List[Dict[str, Any]]]:
    """Get all adversarial test categories."""
    generator = AdversarialTestGenerator()
    return {
        "malformed_ids": generator.generate_malformed_ids(),
        "extreme_values": generator.generate_extreme_values(),
        "prohibited_content": generator.generate_prohibited_content(),
        "oversized_inputs": generator.generate_oversized_inputs(),
        "unicode_edge_cases": generator.generate_unicode_edge_cases(),
        "temporal_adversarial": generator.generate_temporal_adversarial(),
        "resource_exhaustion": generator.generate_resource_exhaustion_cases(),
        "special_numeric_values": generator.generate_special_numeric_values()
    }


# Export public interface
__all__ = [
    "AdversarialTestGenerator",
    "get_malformed_ids",
    "get_extreme_values",
    "get_prohibited_content",
    "get_oversized_inputs",
    "get_unicode_edge_cases",
    "get_temporal_adversarial",
    "get_resource_exhaustion_cases",
    "get_special_numeric_values",
    "get_all_adversarial_categories"
]


# Import sys at the module level for sys.maxsize usage
import sys