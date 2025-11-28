from tools import execute_query


if __name__ == "__main__":
    result = execute_query("SELECT * FROM actor")
    print(result)