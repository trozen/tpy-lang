# Regression: @model field names must not leak into other methods' scopes.
# A @model with field `color: str` must not shadow a `color: Color` parameter
# in an unrelated method after an if-statement triggers _sync_promoted_var_types.
from tpy import Int32, copy
from tplib.json import JsonError, JsonReader, JsonToken, JsonWriter
from tplib.json.model import model
from dataclasses import dataclass
from enum import Enum, auto

class Color(Enum):
    RED = auto()
    BLUE = auto()

@model
class Msg:
    color: str = ""
    value: Int32 = 0

@dataclass
class Item:
    color: Color

class Registry:
    items: dict[Int32, Item]

    def __init__(self):
        self.items = dict[Int32, Item]()

    def update(self, key: Int32, color: Color) -> None:
        if key in self.items:
            del self.items[key]
        item = Item(color)
        self.items[key] = copy(item)

def main() -> None:
    msg = Msg.from_json('{"color": "RED", "value": 42}')
    print(msg.color)
    print(msg.value)

    r = Registry()
    r.update(1, Color.RED)
    r.update(2, Color.BLUE)
    r.update(1, Color.BLUE)
    print(r.items[1].color.name)
    print(r.items[2].color.name)

main()
