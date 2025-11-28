import os
from dotenv import load_dotenv
import psycopg2

# Load environment variables from .env file
load_dotenv()


def execute_query(query: str, params: tuple = None):
    """
    Executes a SQL query using DATABASE_URL from .env and returns the result.

    Parameters:
        query (str): SQL query to execute.
        params (tuple, optional): Parameters for parameterized queries.

    Returns:
        list[tuple] or str: Query result or execution message.
    """
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError("DATABASE_URL not found in .env file")

    connection = None
    result = None

    try:
        # Connect to the PostgreSQL database
        connection = psycopg2.connect(database_url)
        cursor = connection.cursor()

        # Execute the query (supports parameterized queries)
        cursor.execute(query, params)

        if query.strip().lower().startswith("select"):
            result = cursor.fetchall()
        else:
            connection.commit()
            result = f"✅ Query executed successfully ({cursor.rowcount} rows affected)"

    except Exception as e:
        print("❌ Error executing query:", e)
    finally:
        if connection:
            cursor.close()
            connection.close()

    return result
