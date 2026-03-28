# Test @model macro: all type combos, nesting, and round-trip.
# Covers: str, Int32, float, Float32, bool, BigInt, enum, Optional,
# list[T], dict[str, V], tuple, nested @model, list[Model], list[Enum],
# dict[str, list[T]].
from tpy import Int32, Float32, try_parse
from enum import Enum
from tplib.json.model import model

class Role(Enum):
    Admin = 0
    User = 1
    Guest = 2

@model
class Address:
    street: str
    city: str

@model
class Profile:
    name: str
    age: Int32
    score: float
    precision: Float32
    active: bool
    big_id: int
    role: Role
    address: Address
    tags: list[str]
    scores: list[Int32]
    friends: list[Address]
    roles: list[Role]
    metadata: dict[str, Int32]
    nested_map: dict[str, list[Int32]]
    coord: tuple[Int32, Int32, str]
    backup_role: Role | None = None
    alt_address: Address | None = None
    email: str | None = None

def test_full() -> None:
    json = '{"name": "Alice", "age": 30, "score": 9.5, "precision": 1.5, "active": true, "big_id": "999999999999999999", "role": "Admin", "address": {"street": "123 Main", "city": "NYC"}, "tags": ["dev", "ops"], "scores": [100, 95], "friends": [{"street": "456 Oak", "city": "LA"}], "roles": ["User", "Guest"], "metadata": {"level": 5, "xp": 1200}, "nested_map": {"a": [1, 2], "b": [3]}, "coord": [10, 20, "north"], "backup_role": "Guest", "alt_address": {"street": "999 Pine", "city": "CHI"}, "email": "a@b.com"}'
    p = Profile.from_json(json)
    print(p.name)
    print(p.age)
    print(p.score)
    print(p.precision)
    print(p.active)
    print(p.big_id)
    print(p.role.name)
    print(p.address.city)
    print(p.tags)
    print(p.scores)
    print(p.friends[0].city)
    print(p.roles[0].name)
    print(p.roles[1].name)
    print(p.metadata["xp"])
    print(p.nested_map["a"])
    print(p.nested_map["b"])
    print(p.coord)
    print(p.backup_role)
    print(p.alt_address)
    print(p.email)

def test_defaults() -> None:
    json = '{"name": "Bob", "age": 25, "score": 0.0, "precision": 0.0, "active": false, "big_id": "0", "role": "Guest", "address": {"street": "x", "city": "y"}, "tags": [], "scores": [], "friends": [], "roles": [], "metadata": {}, "nested_map": {}, "coord": [0, 0, ""]}'
    p = Profile.from_json(json)
    print(p.name)
    print(p.backup_role)
    print(p.alt_address)
    print(p.email)

def test_roundtrip() -> None:
    json = '{"name": "Eve", "age": 40, "score": 3.14, "precision": 2.5, "active": true, "big_id": "12345678901234567890", "role": "User", "address": {"street": "789 Elm", "city": "SF"}, "tags": ["ops"], "scores": [42], "friends": [{"street": "1st", "city": "DC"}], "roles": ["Admin"], "metadata": {"rank": 1}, "nested_map": {"z": [9]}, "coord": [100, 200, "east"], "alt_address": {"street": "2nd", "city": "BOS"}, "email": null}'
    p = Profile.from_json(json)
    out = p.to_json()
    print(out)
    p2 = Profile.from_json(out)
    print(p == p2)

test_full()
test_defaults()
test_roundtrip()
