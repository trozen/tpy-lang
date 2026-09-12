# Test panic on invalid enum value in JSON.
from tpy import int32, try_parse
from enum import Enum
from tplib.json.model import model

class Color(Enum):
    Red = 0
    Blue = 1

@model
class Item:
    name: str
    color: Color

def main() -> None:
    Item.from_json('{"name": "x", "color": "Purple"}')

main()
