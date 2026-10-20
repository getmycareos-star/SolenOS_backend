def test_care_graph_api_syntax():
    """Test that the care_graph.py file has valid Python syntax."""
    # This test just checks that the file can be imported without syntax errors
    # We don't actually run the application since that would require the full stack
    import ast
    import sys
    
    # Read the file
    with open("C:\\solenos\\backend\\app\\api\\care_graph.py", "r") as f:
        content = f.read()
    
    # Parse it to check for syntax errors
    try:
        tree = ast.parse(content)
        print("Syntax check PASSED: care_graph.py has valid Python syntax")
        return True
    except SyntaxError as e:
        print(f"Syntax check FAILED: {e}")
        return False


def test_care_graph_api_routes():
    """Test that care_graph.py contains all expected route definitions."""
    expected_routes = [
        '@router.post("/{person_id}/nodes"',
        '@router.get("/{person_id}/nodes/{node_id}"',
        '@router.put("/{person_id}/nodes/{node_id}"',
        '@router.delete("/{person_id}/nodes/{node_id}"',
        '@router.post("/{person_id}/edges"',
        '@router.get("/{person_id}/edges/{edge_id}"',
        '@router.put("/{person_id}/edges/{edge_id}"',
        '@router.delete("/{person_id}/edges/{edge_id}"',
        '@router.get("/{person_id}/snapshot"'
    ]
    
    # Read the file
    with open("C:\\solenos\\backend\\app\\api\\care_graph.py", "r") as f:
        content = f.read()
    
    # Check that all expected routes are present
    missing_routes = []
    for route in expected_routes:
        if route not in content:
            missing_routes.append(route)
    
    if missing_routes:
        print(f"FAILED: Missing routes: {missing_routes}")
        return False
    else:
        print("PASSED: All expected routes found in care_graph.py")
        return True


def test_care_graph_api_person_boundary():
    """Test that care_graph.py contains person boundary enforcement."""
    # Read the file
    with open("C:\\solenos\\backend\\app\\api\\care_graph.py", "r") as f:
        content = f.read()
    
    # Check for verify_person_access function
    if "def verify_person_access" not in content:
        print("FAILED: verify_person_access function not found")
        return False
    
    # Check that it's called in the right places
    expected_calls = [
        "verify_person_access(person_id, current_user)"  # Should be in each endpoint
    ]
    
    # Count occurrences - should be at least 8 (test + 4 node + 4 edge + 1 snapshot)
    call_count = content.count("verify_person_access(person_id, current_user)")
    if call_count >= 8:
        print(f"PASSED: Found {call_count} calls to verify_person_access")
        return True
    else:
        print(f"FAILED: Expected at least 8 calls to verify_person_access, found {call_count}")
        return False


if __name__ == "__main__":
    print("Running care_graph.py validation tests...")
    
    syntax_ok = test_care_graph_api_syntax()
    routes_ok = test_care_graph_api_routes()
    boundary_ok = test_care_graph_api_person_boundary()
    
    if syntax_ok and routes_ok and boundary_ok:
        print("\n*** All validation tests PASSED ***")
        exit(0)
    else:
        print("\n!!! Some validation tests FAILED !!!")
        exit(1)