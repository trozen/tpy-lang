# Test @model macro: basic serialization/deserialization, round-trip, optionals.
from tpy import Int32
from tplib.json import JsonReader, JsonToken, JsonWriter
from tplib.json.model import model

@model
class User:
    name: str
    age: Int32
    active: bool
    email: str | None = None

def test_deserialize() -> None:
    user = User.from_json('{"name": "Alice", "age": 30, "active": true, "email": "a@b.com"}')
    print(user.name)
    print(user.age)
    print(user.active)
    print(user.email)

def test_optional_missing() -> None:
    user = User.from_json('{"name": "Bob", "age": 25, "active": false}')
    print(user.name)
    print(user.email)

def test_optional_null() -> None:
    user = User.from_json('{"name": "Eve", "age": 40, "active": true, "email": null}')
    print(user.email)

def test_serialize() -> None:
    user = User("Alice", 30, True, "a@b.com")
    print(user.to_json())

def test_serialize_null() -> None:
    user = User("Bob", 25, False, None)
    print(user.to_json())

def test_roundtrip() -> None:
    json = '{"name": "Eve", "age": 40, "active": true, "email": null}'
    user = User.from_json(json)
    user2 = User.from_json(user.to_json())
    print(user == user2)

def test_skip_unknown() -> None:
    user = User.from_json('{"name": "X", "extra": 999, "age": 1, "active": false}')
    print(user.name)
    print(user.age)

test_deserialize()
test_optional_missing()
test_optional_null()
test_serialize()
test_serialize_null()
test_roundtrip()
test_skip_unknown()
