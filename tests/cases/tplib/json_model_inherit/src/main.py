# @model inheritance: parent fields included in JSON serialization, multi-level.
from tpy import Int32
from tplib.json.model import model
from typing import Optional

@model
class Base:
    name: str
    age: Int32

@model
class User(Base):
    email: str

# Multi-level: grandparent -> parent -> child
@model
class Admin(User):
    role: str

# Defaults in parent propagated to child
@model
class WithDefaults:
    x: Int32
    y: Int32 = 0

@model
class Extended(WithDefaults):
    z: Int32 = 99

# Optional parent field + child field
@model
class Tagged:
    tag: str
    note: Optional[str] = None

@model
class Scored(Tagged):
    score: Int32 = 0


def main() -> None:
    # Single-level inheritance: round-trip
    u = User("Alice", 30, "alice@example.com")
    s = u.to_json()
    print(s)
    u2 = User.from_json(s)
    print(u2.name, u2.age, u2.email)

    # Multi-level inheritance
    a = Admin("Bob", 40, "bob@co.com", "superuser")
    s2 = a.to_json()
    print(s2)
    a2 = Admin.from_json(s2)
    print(a2.name, a2.age, a2.email, a2.role)

    # Defaults: only required field provided
    ed = Extended(1)
    print(ed.to_json())
    # All fields provided
    ed2 = Extended(1, 2, 3)
    print(ed2.to_json())
    # Round-trip with defaults
    ed3 = Extended.from_json('{"x": 10}')
    print(ed3.x, ed3.y, ed3.z)

    # Optional parent fields
    sc = Scored("hello", None, 42)
    print(sc.to_json())
    # Decode with missing optional + default
    sc2 = Scored.from_json('{"tag": "hi", "score": 7}')
    print(sc2.tag, sc2.note, sc2.score)

    # Equality across inherited fields
    print(User("A", 1, "a") == User("A", 1, "a"))
    print(User("A", 1, "a") == User("A", 1, "b"))

main()
