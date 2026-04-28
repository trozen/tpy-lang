# Recursive union with both int and float as members.
# Returning an int literal must land on the int variant -- not silently
# coerce to float because float happens to be earlier in the canonical
# member ordering. Same for assignment into list/dict elements typed as V.
from tpy import Own

type V = None | bool | int | float | str | list[V] | dict[str, V]


def make_int() -> Own[V]:
    return 42


def make_float() -> Own[V]:
    return 3.14


def make_null() -> Own[V]:
    # Direct `return None` from a function returning Own[RecursiveUnion]
    # must construct the wrapper struct's monostate alternative -- not nullptr.
    return None


def make_mixed_list() -> Own[V]:
    return [1, 2.5, 3, 4.5]


def make_mixed_dict() -> Own[V]:
    return {"i": 7, "f": 0.25, "s": "hi", "n": None}


def kind(v: V) -> str:
    match v:
        case None:
            return "null"
        case bool() as b:
            return "bool"
        case int() as n:
            return "int"
        case float() as f:
            return "float"
        case str() as s:
            return "str"
        case list() as items:
            return "list"
        case dict() as d:
            return "dict"


def main() -> None:
    a = make_int()
    b = make_float()
    print(a)
    print(b)
    print(kind(a))
    print(kind(b))

    n = make_null()
    print(n)
    print(kind(n))

    xs = make_mixed_list()
    print(xs)
    print(kind(xs))

    d = make_mixed_dict()
    print(d)
    print(kind(d))


main()
