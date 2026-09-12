# Test @model and @dataclass with field(default_factory=list) in the same module.
# Regression: macro_deps pulled stdlib into user deps, skipping marker protocol
# registration (Default), which broke default_factory validation.
from tpy import int32
from tplib.json.model import model
from dataclasses import dataclass, field

@model
class Item:
    name: str
    value: int32

@dataclass
class Container:
    label: str
    items: list[Item] = field(default_factory=list)

def main() -> None:
    c = Container("empty")
    print(c.label)
    print(len(c.items))

    item = Item.from_json('{"name": "widget", "value": 42}')
    c2 = Container("one", [item])
    print(c2.label)
    print(len(c2.items))
    print(c2.items[0].name)

main()
