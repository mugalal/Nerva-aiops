from pathlib import Path

from .connection import connect


def init_db() -> None:
    sql = (Path(__file__).parent / "schema.sql").read_text()
    with connect() as conn:
        conn.execute(sql)


def main() -> None:
    init_db()
    print("database initialized")


if __name__ == "__main__":
    main()