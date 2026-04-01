# Test @model error messages via try_from_json: missing field, invalid enum, describe().
from enum import Enum
from tplib.json import JsonError
from tplib.json.model import model

class Color(Enum):
    Red = 0
    Blue = 1

@model
class Item:
    name: str
    color: Color

def test_missing_field() -> None:
    data = '{"name": "x"}'
    try:
        Item.try_from_json(data)
    except JsonError as e:
        print(e.message)

def test_invalid_enum() -> None:
    data = '{"name": "x", "color": "Purple"}'
    try:
        Item.try_from_json(data)
    except JsonError as e:
        print(e.message)

def test_malformed_with_describe() -> None:
    data = '{"name": "x", "color"  123}'
    try:
        Item.try_from_json(data)
    except JsonError as e:
        print(e.describe(data))

def main() -> None:
    test_missing_field()
    test_invalid_enum()
    test_malformed_with_describe()

main()
