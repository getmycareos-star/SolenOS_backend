from app.core.database import _connect_args_for_database


def test_sqlite_receives_thread_check_argument():
    assert _connect_args_for_database("sqlite:///./solenos.db") == {
        "check_same_thread": False
    }


def test_postgres_does_not_receive_sqlite_arguments():
    assert _connect_args_for_database("postgresql://user:pass@localhost/solenos") == {}
