"""
Security test suite for SQL query validation.
Run this before deploying to production.
"""

import os
from dotenv import load_dotenv
from sql_tools import SQLQueryValidator, SecureSQLExecutor

load_dotenv()

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'


def print_test(name: str, passed: bool, message: str = ""):
    """Print test result with color."""
    status = f"{GREEN}✓ PASS{RESET}" if passed else f"{RED}✗ FAIL{RESET}"
    print(f"{status} - {name}")
    if message:
        print(f"     {message}")


def test_query_validation():
    """Test SQL query validation rules."""
    print(f"\n{YELLOW}=== Testing Query Validation ==={RESET}\n")

    validator = SQLQueryValidator()

    # Test 1: Valid SELECT query
    result = validator.validate("SELECT * FROM users WHERE id = 1")
    print_test("Valid SELECT query", result['valid'])

    # Test 2: Blocked DROP statement
    result = validator.validate("DROP TABLE users")
    print_test(
        "Block DROP statement",
        not result['valid'] and 'DROP' in result.get('error', ''),
        result.get('error', '')
    )

    # Test 3: Blocked DELETE statement
    result = validator.validate("DELETE FROM users WHERE id = 1")
    print_test(
        "Block DELETE statement",
        not result['valid'] and 'DELETE' in result.get('error', ''),
        result.get('error', '')
    )

    # Test 4: Blocked UPDATE statement
    result = validator.validate("UPDATE users SET name = 'hacked'")
    print_test(
        "Block UPDATE statement",
        not result['valid'] and 'UPDATE' in result.get('error', ''),
        result.get('error', '')
    )

    # Test 5: Blocked INSERT statement
    result = validator.validate("INSERT INTO users (name) VALUES ('test')")
    print_test(
        "Block INSERT statement",
        not result['valid'] and 'INSERT' in result.get('error', ''),
        result.get('error', '')
    )

    # Test 6: Multiple statements (SQL injection)
    result = validator.validate("SELECT * FROM users; DROP TABLE users")
    print_test(
        "Block multiple statements",
        not result['valid'] and 'Multiple' in result.get('error', ''),
        result.get('error', '')
    )

    # Test 7: SQL comments (injection vector)
    result = validator.validate("SELECT * FROM users -- WHERE admin = 1")
    print_test(
        "Block SQL comments",
        not result['valid'] and 'comment' in result.get('error', '').lower(),
        result.get('error', '')
    )

    # Test 8: Query too long
    long_query = "SELECT * FROM " + ("users, " * 500)
    result = validator.validate(long_query)
    print_test(
        "Block queries exceeding length limit",
        not result['valid'] and 'long' in result.get('error', '').lower(),
        result.get('error', '')
    )

    # Test 9: Empty query
    result = validator.validate("")
    print_test(
        "Block empty queries",
        not result['valid'],
        result.get('error', '')
    )

    # Test 10: Valid query with LIMIT
    result = validator.validate("SELECT id, name FROM users ORDER BY created_at DESC LIMIT 10")
    print_test("Valid query with LIMIT", result['valid'])

    # Test 11: Valid query with JOIN
    result = validator.validate("""
        SELECT u.name, COUNT(m.id) as message_count 
        FROM users u 
        LEFT JOIN messages m ON u.id = m.user_id 
        GROUP BY u.name
    """)
    print_test("Valid query with JOIN", result['valid'])

    # Test 12: Blocked EXEC
    result = validator.validate("EXEC sp_executesql 'DROP TABLE users'")
    print_test(
        "Block EXEC statement",
        not result['valid'] and 'EXEC' in result.get('error', ''),
        result.get('error', '')
    )

    # Test 13: Blocked GRANT
    result = validator.validate("GRANT ALL PRIVILEGES ON users TO hacker")
    print_test(
        "Block GRANT statement",
        not result['valid'] and 'GRANT' in result.get('error', ''),
        result.get('error', '')
    )


def test_rate_limiting():
    """Test rate limiting functionality."""
    print(f"\n{YELLOW}=== Testing Rate Limiting ==={RESET}\n")

    validator = SQLQueryValidator()
    test_user = "test_user_123"

    # Make requests up to the limit
    successes = 0
    for i in range(12):
        result = validator.validate("SELECT 1", user_id=test_user)
        if result['valid']:
            successes += 1

    print_test(
        "Rate limiting enforced",
        successes <= 10,
        f"Allowed {successes}/12 requests (limit is 10)"
    )


def test_database_connection():
    """Test actual database connection and query execution."""
    print(f"\n{YELLOW}=== Testing Database Connection ==={RESET}\n")

    sample_db_url = os.getenv('SAMPLE_DB_URL')

    if not sample_db_url:
        print_test("Sample Database URL configured", False, "SAMPLE_DB_URL not found in environment")
        return

    print_test("Sample Database URL configured", True)

    # Also check chat database
    chat_db_url = os.getenv('DATABASE_URL')
    if chat_db_url:
        print_test("Chat Database URL configured", True)
    else:
        print_test("Chat Database URL configured", False, "DATABASE_URL not found (needed for app)")

    try:
        executor = SecureSQLExecutor(sample_db_url)

        # Test 1: Get table names
        tables = executor.get_table_names()
        print_test(
            "Retrieve table names",
            len(tables) > 0,
            f"Found {len(tables)} tables: {', '.join(tables)}"
        )

        # Test 2: Execute safe query
        result = executor.execute_query("SELECT 1 as test", user_id="test_user")
        print_test(
            "Execute safe query",
            result['success'],
            f"Returned {result.get('row_count', 0)} rows"
        )

        # Test 3: Block dangerous query
        result = executor.execute_query("DROP TABLE users", user_id="test_user")
        print_test(
            "Block dangerous query via executor",
            not result['success'],
            result.get('error', '')
        )

        # Test 4: Auto-add LIMIT
        result = executor.execute_query("SELECT * FROM users", user_id="test_user", max_rows=5)
        print_test(
            "Auto-limit query results",
            result['success'] and result.get('row_count', 0) <= 5,
            f"Limited to {result.get('row_count', 0)} rows"
        )

    except Exception as e:
        print_test("Database connection", False, str(e))


def test_sql_injection_attempts():
    """Test common SQL injection patterns."""
    print(f"\n{YELLOW}=== Testing SQL Injection Protection ==={RESET}\n")

    validator = SQLQueryValidator()

    injection_attempts = [
        ("' OR '1'='1", "Classic OR injection"),
        ("1; DROP TABLE users--", "Statement termination"),
        ("'; DROP TABLE users--", "Quote escape with drop"),
        ("UNION SELECT password FROM users", "UNION-based injection"),
        ("' OR 1=1--", "Always true condition"),
        ("admin'--", "Comment-based injection"),
        ("' OR 'a'='a", "String comparison injection"),
        ("1' AND '1'='1", "AND injection"),
    ]

    blocked_count = 0
    for injection, description in injection_attempts:
        query = f"SELECT * FROM users WHERE username = '{injection}'"
        result = validator.validate(query)

        if not result['valid']:
            blocked_count += 1

        print_test(
            f"Block: {description}",
            not result['valid'],
            result.get('error', '') if not result['valid'] else "⚠️  NOT BLOCKED"
        )

    print(f"\n{GREEN}Blocked {blocked_count}/{len(injection_attempts)} injection attempts{RESET}")


def test_performance():
    """Test validation performance."""
    print(f"\n{YELLOW}=== Testing Performance ==={RESET}\n")

    import time

    validator = SQLQueryValidator()

    # Test validation speed
    iterations = 1000
    start_time = time.time()

    for _ in range(iterations):
        validator.validate("SELECT * FROM users WHERE id = 1")

    end_time = time.time()
    avg_time = (end_time - start_time) / iterations * 1000  # milliseconds

    print_test(
        "Validation performance",
        avg_time < 10,  # Should be under 10ms per validation
        f"Average: {avg_time:.3f}ms per validation"
    )


def run_all_tests():
    """Run all security tests."""
    print(f"\n{YELLOW}{'='*60}{RESET}")
    print(f"{YELLOW}  SQL Security Test Suite{RESET}")
    print(f"{YELLOW}{'='*60}{RESET}")

    test_query_validation()
    test_rate_limiting()
    test_sql_injection_attempts()
    test_performance()
    test_database_connection()

    print(f"\n{YELLOW}{'='*60}{RESET}")
    print(f"{YELLOW}  Tests Complete{RESET}")
    print(f"{YELLOW}{'='*60}{RESET}\n")


if __name__ == "__main__":
    run_all_tests()