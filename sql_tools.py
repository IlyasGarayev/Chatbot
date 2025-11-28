import os
import re
import sqlparse
from typing import Optional, Dict, Any

from langchain_community.document_loaders.notiondb import DATABASE_URL
from langchain_core.tools import Tool
from langchain_community.utilities import SQLDatabase
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
import time
from collections import defaultdict
from threading import Lock
from langchain.tools import tool
import dotenv


class SQLQueryValidator:
    """Validates and sanitizes SQL queries before execution."""

    # Blocked SQL keywords that could modify data
    BLOCKED_KEYWORDS = [
        'DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE',
        'TRUNCATE', 'GRANT', 'REVOKE', 'EXEC', 'EXECUTE',
        'INTO OUTFILE', 'LOAD_FILE', 'LOAD DATA', 'BACKUP'
    ]

    DATABASE_URL = os.getenv("DATABASE_URL")

    # Maximum query length
    MAX_QUERY_LENGTH = 2000

    # Maximum number of tables in a query
    MAX_TABLES = 5

    def __init__(self):
        self.rate_limiter = RateLimiter(max_requests=10, time_window=60)
        dotenv.load_dotenv()

    def validate(self, query: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Validate a SQL query against security rules.

        Returns:
            Dict with 'valid' (bool) and 'error' (str) if invalid
        """
        # 1. Rate limiting check
        if user_id and not self.rate_limiter.allow_request(user_id):
            return {
                'valid': False,
                'error': 'Rate limit exceeded. Please wait before making more queries.'
            }

        # 2. Length validation
        if len(query) > self.MAX_QUERY_LENGTH:
            return {
                'valid': False,
                'error': f'Query too long. Maximum length is {self.MAX_QUERY_LENGTH} characters.'
            }

        # 3. Check for empty query
        if not query or not query.strip():
            return {
                'valid': False,
                'error': 'Query cannot be empty.'
            }

        # 4. Parse and validate SQL structure
        try:
            parsed = sqlparse.parse(query)
            if not parsed:
                return {'valid': False, 'error': 'Invalid SQL syntax.'}

            # Get the first statement
            statement = parsed[0]

            # 5. Check query type - must be SELECT
            query_type = statement.get_type()
            if query_type != 'SELECT':
                return {
                    'valid': False,
                    'error': f'Only SELECT queries are allowed. Found: {query_type}'
                }

        except Exception as e:
            return {
                'valid': False,
                'error': f'SQL parsing error: {str(e)}'
            }

        # 6. Check for blocked keywords
        query_upper = query.upper()
        for keyword in self.BLOCKED_KEYWORDS:
            # Use word boundaries to avoid false positives
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, query_upper):
                return {
                    'valid': False,
                    'error': f'Blocked keyword detected: {keyword}'
                }

        # 7. Check for SQL comments (potential injection)
        if '--' in query or '/*' in query or '*/' in query:
            return {
                'valid': False,
                'error': 'SQL comments are not allowed.'
            }

        # 8. Check for multiple statements (prevent stacked queries)
        if ';' in query.rstrip(';'):
            return {
                'valid': False,
                'error': 'Multiple SQL statements are not allowed.'
            }

        # 9. Validate table count
        table_count = self._count_tables(query)
        if table_count > self.MAX_TABLES:
            return {
                'valid': False,
                'error': f'Too many tables in query. Maximum is {self.MAX_TABLES}.'
            }

        return {'valid': True}

    def _count_tables(self, query: str) -> int:
        """Count the number of tables referenced in the query."""
        # Simple heuristic: count FROM and JOIN occurrences
        query_upper = query.upper()
        from_count = len(re.findall(r'\bFROM\b', query_upper))
        join_count = len(re.findall(r'\bJOIN\b', query_upper))
        return from_count + join_count


class RateLimiter:
    """Simple rate limiter for database queries."""

    def __init__(self, max_requests: int = 10, time_window: int = 60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = defaultdict(list)
        self.lock = Lock()

    def allow_request(self, user_id: str) -> bool:
        """Check if a request is allowed for the given user."""
        with self.lock:
            current_time = time.time()

            # Clean old requests
            self.requests[user_id] = [
                req_time for req_time in self.requests[user_id]
                if current_time - req_time < self.time_window
            ]

            # Check limit
            if len(self.requests[user_id]) >= self.max_requests:
                return False

            # Add new request
            self.requests[user_id].append(current_time)
            return True


class SecureSQLExecutor:
    """Executes validated SQL queries with additional safety measures."""

    def __init__(self, database_url: str, timeout: int = 10):
        self.engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=3600,
            connect_args={'connect_timeout': timeout}
        )
        self.validator = SQLQueryValidator()
        self.db = SQLDatabase(self.engine)

    def execute_query(
            self,
            query: str,
            user_id: Optional[str] = None,
            max_rows: int = 100
    ) -> Dict[str, Any]:
        """
        Execute a validated SQL query safely.

        Args:
            query: SQL query to execute
            user_id: User identifier for rate limiting
            max_rows: Maximum number of rows to return

        Returns:
            Dict with 'success', 'data', and optional 'error'
        """
        # Validate query
        validation_result = self.validator.validate(query, user_id)
        if not validation_result['valid']:
            return {
                'success': False,
                'error': validation_result['error']
            }

        try:
            # Add LIMIT clause if not present
            query_upper = query.upper()
            if 'LIMIT' not in query_upper:
                query = f"{query.rstrip(';')} LIMIT {max_rows}"

            # Execute with timeout
            with self.engine.connect() as connection:
                result = connection.execute(text(query))

                # Fetch results
                rows = result.fetchall()
                columns = result.keys()

                # Convert to list of dicts
                data = [dict(zip(columns, row)) for row in rows]

                return {
                    'success': True,
                    'data': data,
                    'row_count': len(data)
                }

        except SQLAlchemyError as e:
            return {
                'success': False,
                'error': f'Database error: {str(e)}'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Unexpected error: {str(e)}'
            }

    def get_table_info(self) -> str:
        """Get information about available tables."""
        try:
            return self.db.get_table_info()
        except Exception as e:
            return f"Error fetching table info: {str(e)}"

    def get_table_names(self) -> list:
        """Get list of table names."""
        try:
            return self.db.get_usable_table_names()
        except Exception as e:
            return []


@tool
def create_sql_query_tool(database_url: str = DATABASE_URL) -> Tool:
    """
    Create a LangChain tool for secure SQL queries.

    Args:
        database_url: PostgreSQL connection string

    Returns:
        LangChain Tool instance
    """
    executor = SecureSQLExecutor(database_url)

    def run_query(query: str) -> str:
        """Execute a SQL query and return formatted results."""
        # Extract user_id from context if available (you can enhance this)
        result = executor.execute_query(query, user_id="default_user")

        if not result['success']:
            return f"❌ Query failed: {result['error']}"

        data = result['data']
        row_count = result['row_count']

        if not data:
            return "✓ Query executed successfully but returned no results."

        # Format results as a readable string
        output = f"✓ Query returned {row_count} row(s):\n\n"

        # Simple table formatting
        if data:
            headers = list(data[0].keys())
            output += " | ".join(headers) + "\n"
            output += "-" * (len(output) - 2) + "\n"

            for row in data[:10]:  # Show first 10 rows
                output += " | ".join(str(row.get(h, '')) for h in headers) + "\n"

            if row_count > 10:
                output += f"\n... and {row_count - 10} more rows"

        return output

    return Tool(
        name="query_database",
        description="""
        Execute a SELECT query on the PostgreSQL database to retrieve statistical information.

        IMPORTANT RULES:
        - Only SELECT queries are allowed
        - No data modification (INSERT, UPDATE, DELETE, DROP, etc.)
        - Queries are automatically limited to 100 rows
        - Rate limited to 10 queries per minute

        Use this tool when users ask for:
        - Statistics, counts, or aggregations
        - Data from specific tables
        - Filtered or grouped information

        Input should be a valid SQL SELECT statement.
        Example: "SELECT COUNT(*) FROM users WHERE created_at > '2024-01-01'"
        """,
        func=run_query
    )

@tool
def create_schema_info_tool(database_url: str = DATABASE_URL) -> Tool:
    """
    Create a tool that provides database schema information.

    Args:
        database_url: PostgreSQL connection string

    Returns:
        LangChain Tool instance
    """
    executor = SecureSQLExecutor(database_url)

    def get_schema_info(query: str = "") -> str:
        """Get database schema information."""
        table_info = executor.get_table_info()
        table_names = executor.get_table_names()

        output = "📊 Database Schema Information:\n\n"
        output += f"Available tables: {', '.join(table_names)}\n\n"
        output += table_info

        return output

    return Tool(
        name="get_database_schema",
        description="""
        Get information about the database schema including table names, 
        column names, and data types.

        Use this tool when you need to:
        - Understand what tables are available
        - Learn about table structure and columns
        - Plan a query before executing it

        Input can be empty or a specific table name.
        """,
        func=get_schema_info
    )