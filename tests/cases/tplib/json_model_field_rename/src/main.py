# @model field renaming: alias maps Python field name to JSON key.
from tpy import int32
from typing import Optional
from tplib.json.model import model, field

@model
class User:
    first_name: str = field(alias="firstName")
    last_name: str = field(alias="lastName")
    age: int32

@model
class WithDefault:
    label: str = field(alias="lbl", default="none")
    note: Optional[str] = field(default=None)
    score: int32 = 0

@model
class Base:
    item_id: int32 = field(alias="id")

@model
class Extended(Base):
    label: str

def main() -> None:
    # Serialize: Python field names -> JSON aliases
    u = User("Alice", "Smith", 30)
    s = u.to_json()
    print(s)

    # Deserialize: JSON aliases -> Python field names
    u2 = User.from_json('{"firstName": "Bob", "lastName": "Jones", "age": 25}')
    print(u2.first_name, u2.last_name, u2.age)

    # Round-trip
    u3 = User.from_json(u.to_json())
    print(u == u3)

    # Alias with default, field(default=None)
    w = WithDefault()
    print(w.to_json())
    w2 = WithDefault.from_json('{"lbl": "hi", "score": 7}')
    print(w2.label, w2.note, w2.score)

    # Inherited alias
    e = Extended(42, "hello")
    s2 = e.to_json()
    print(s2)
    e2 = Extended.from_json('{"id": 99, "label": "world"}')
    print(e2.item_id, e2.label)

main()
