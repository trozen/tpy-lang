# Test panic on missing required field in @model deserialization.
from tpy import Int32, try_parse
from enum import Enum
from tplib.json import JsonError, JsonReader, JsonToken, JsonWriter
from tplib.json.model import model

class Color(Enum):
    Red = 0

@model
class Item:
    name: str
    color: Color

def main() -> None:
    Item.from_json('{"name": "x"}')

main()
