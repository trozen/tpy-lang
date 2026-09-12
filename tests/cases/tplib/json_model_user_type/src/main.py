# User-defined types as @model fields via __json_encode__/__json_decode__.
from __future__ import annotations
from typing import Optional
from tpy import int32, Own, error_return
from tplib.json import JsonReader, JsonWriter, JsonError
from tplib.json.model import model

class Seconds:
    """User type with custom JSON serialization (encodes as integer)."""
    _value: int32

    def __init__(self, value: int32) -> None:
        self._value = value

    def __eq__(self, other: Seconds) -> bool:
        return self._value == other._value

    def __json_encode__(self, writer: JsonWriter) -> None:
        writer.write_int32(self._value)

    @staticmethod
    @error_return(JsonError)
    def __json_decode__(reader: JsonReader) -> Own[Seconds]:
        raw = reader.read_int()
        return Seconds(int32(raw))

@model
class Event:
    name: str
    when: Seconds

@model
class Schedule:
    events: list[Event]
    default_duration: Seconds
    deadline: Optional[Seconds] = None

def test_roundtrip() -> None:
    e = Event("deploy", Seconds(3600))
    s = e.to_json()
    print(s)
    e2 = Event.from_json(s)
    print(e2.name)
    print(e2.when._value)
    print(e == e2)

def test_nested() -> None:
    sc = Schedule(
        [Event("a", Seconds(10)), Event("b", Seconds(20))],
        Seconds(60),
    )
    s = sc.to_json()
    print(s)
    sc2 = Schedule.from_json(s)
    print(len(sc2.events))
    print(sc2.events[0].when._value)
    print(sc2.default_duration._value)

def test_optional() -> None:
    sc = Schedule(
        [Event("x", Seconds(1))],
        Seconds(30),
        Seconds(99),
    )
    s = sc.to_json()
    print(s)
    sc2 = Schedule.from_json(s)
    d = sc2.deadline
    if d is not None:
        print(d._value)

    sc3 = Schedule([Event("y", Seconds(2))], Seconds(30))
    s3 = sc3.to_json()
    print(s3)
    sc4 = Schedule.from_json(s3)
    print(sc4.deadline is None)

test_roundtrip()
test_nested()
test_optional()
