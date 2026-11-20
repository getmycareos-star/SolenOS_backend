import json
from sqlalchemy import TypeDecorator
from sqlalchemy.types import TEXT


class JSONList(TypeDecorator):
    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # PostgreSQL native JSON columns are decoded by the DBAPI before this
        # TypeDecorator runs. SQLite/TEXT-backed columns still arrive as JSON
        # strings. Accept both representations without decoding a Python list
        # or dict a second time.
        if not isinstance(value, (str, bytes, bytearray)):
            return value
        return json.loads(value)
