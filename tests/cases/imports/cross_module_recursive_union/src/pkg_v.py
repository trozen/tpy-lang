# Recursive union alias used cross-module. Defines V and producers; main.py
# consumes via imported function calls and exercises codegen qualification.
from tpy import Own

type V = None | bool | int | str | list[V] | dict[str, V]


def make_int() -> Own[V]:
    return 7


def make_dict() -> Own[V]:
    d: dict[str, V] = {"k": 1, "n": None, "items": [1, None, "x"]}
    return d


def kind(v: V) -> str:
    match v:
        case None:
            return "null"
        case bool() as b:
            return "bool"
        case int() as n:
            return "int"
        case str() as s:
            return "str"
        case list() as items:
            return "list"
        case dict() as d:
            return "dict"
