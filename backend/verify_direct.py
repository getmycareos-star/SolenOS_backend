"""
Direct verification of Person Responsibility model files.
This test reads the model files directly to verify their correctness without importing the full app.
"""

import re
import sys
import os

def check_model_file(filepath, expected_classname, expected_tablename):
    """Check that a model file contains the expected class and table name."""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Check if class is defined
        class_pattern = rf'class\s+{expected_classname}\s*\('
        if not re.search(class_pattern, content):
            print(f"FAIL: {expected_classname}: Class not found in file")
            return False
        
        # Check if __tablename__ is correct
        tablename_pattern = rf'__tablename__\s*=\s*["\']{expected_tablename}["\']'
        if not re.search(tablename_pattern, content):
            print(f"FAIL: {expected_classname}: __tablename__ not correct or missing")
            return False
        
        print(f"PASS: {expected_classname}: Correctly defined with __tablename__ = '{expected_tablename}'")
        return True
            
    except Exception as e:
        print(f"ERROR: Error checking {filepath}: {e}")
        return False

def check_schema_file(filepath, expected_classes):
    """Check that a schema file contains the expected classes."""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Check if all expected classes are defined
        missing_classes = []
        for classname in expected_classes:
            class_pattern = rf'class\s+{classname}\s*\('
            if not re.search(class_pattern, content):
                missing_classes.append(classname)
        
        if not missing_classes:
            print(f"PASS: Schema file contains all expected classes: {', '.join(expected_classes)}")
            return True
        else:
            print(f"FAIL: Schema file missing classes: {', '.join(missing_classes)}")
            return False
            
    except Exception as e:
        print(f"ERROR: Error checking schema file {filepath}: {e}")
        return False

def check_api_file(filepath, expected_routes):
    """Check that an API file contains the expected route patterns."""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Check for expected route patterns
        missing_routes = []
        for route in expected_routes:
            # Look for the route in @router decorators or path definitions
            route_patterns = [
                rf'@router\.(get|post|put|delete|patch)\(["\']{re.escape(route)}["\']',
                rf'path\(["\']{re.escape(route)}["\']',
                rf'["\']{re.escape(route)}["\']'
            ]
            
            found = False
            for pattern in route_patterns:
                if re.search(pattern, content):
                    found = True
                    break
            
            if not found:
                missing_routes.append(route)
        
        if not missing_routes:
            print(f"PASS: API file contains all expected routes: {', '.join(expected_routes)}")
            return True
        else:
            print(f"FAIL: API file missing routes: {', '.join(missing_routes)}")
            return False
            
    except Exception as e:
        print(f"ERROR: Error checking API file {filepath}: {e}")
        return False

def main():
    """Run all verification tests."""
    print("=== Direct Verification of Person Responsibility Implementation ===\n")
    
    # Test model files
    model_tests = [
        ("C:\\solenos\\backend\\app\\models\\person_relationship.py", "PersonRelationship", "person_relationships"),
        ("C:\\solenos\\backend\\app\\models\\person_role.py", "PersonRole", "person_roles"),
        ("C:\\solenos\\backend\\app\\models\\person_responsibility.py", "PersonResponsibility", "person_responsibilities"),
        ("C:\\solenos\\backend\\app\\models\\person_availability.py", "PersonAvailability", "person_availabilities"),
        ("C:\\solenos\\backend\\app\\models\\information_contribution.py", "InformationContribution", "information_contributions"),
    ]
    
    # Test schema files
    schema_tests = [
        ("C:\\solenos\\backend\\app\\schemas\\person_responsibility.py", 
         {"PersonRelationshipCreate", "PersonRelationshipResponse",
          "PersonRoleCreate", "PersonRoleResponse",
          "PersonResponsibilityCreate", "PersonResponsibilityResponse",
          "PersonAvailabilityCreate", "PersonAvailabilityResponse",
          "InformationContributionCreate", "InformationContributionResponse"}),
    ]
    
    # Test API files
    api_tests = [
        ("C:\\solenos\\backend\\app\\api\\person_responsibility.py", 
         ["/person-relationships", "/person-roles", "/person-responsibilities", 
          "/person-availabilities", "/information-contributions"]),
    ]
    
    passed = 0
    total = len(model_tests) + len(schema_tests) + len(api_tests)
    
    print("--- Model Files ---")
    for filepath, classname, tablename in model_tests:
        if check_model_file(filepath, classname, tablename):
            passed += 1
    
    print("\n--- Schema Files ---")
    for filepath, expected_classes in schema_tests:
        if check_schema_file(filepath, expected_classes):
            passed += 1
    
    print("\n--- API Files ---")
    for filepath, expected_routes in api_tests:
        if check_api_file(filepath, expected_routes):
            passed += 1
    
    print(f"\n=== Results: {passed}/{total} tests passed ===")
    
    if passed == total:
        print("SUCCESS: All verification tests passed!")
        return True
    else:
        print("FAILURE: Some verification tests failed.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)