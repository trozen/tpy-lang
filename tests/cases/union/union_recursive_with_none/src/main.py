# Recursive type alias with None as a direct member.
# Exercises: function return Own[V], parameter passing, dict/list elements
# carrying None, match dispatch including 'case None:', and 'is None' guards.
from tpy import Own

type V = None | bool | int | str | list[V] | dict[str, V]


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
            return "list/" + str(len(items))
        case dict() as d:
            return "dict/" + str(len(d))


def make_dict() -> Own[V]:
    d: dict[str, V] = {"k": 1, "n": None, "items": [1, None, "x"]}
    return d


def is_null(v: V) -> bool:
    if v is None:
        return True
    return False


def kind_after_null_guard(v: V) -> str:
    # Narrow-then-match: after `is None` strips NoneType, the residual type
    # must still be recognized as the recursive alias so codegen uses the
    # wrapper struct's variant indices.
    if v is None:
        return "null"
    match v:
        case bool() as b:
            return "bool"
        case int() as n:
            return "int"
        case str() as s:
            return "str"
        case list() as items:
            return "list/" + str(len(items))
        case dict() as d:
            return "dict/" + str(len(d))


# Non-recursive 3-way union with None as a direct member: same `case None:`
# match support but goes through the pointer-variant codegen path, not the
# recursive-wrapper-struct path.
type W = int | str | None


def flat_kind(v: W) -> str:
    match v:
        case None:
            return "null"
        case int() as n:
            return "i:" + str(n)
        case str() as s:
            return "s:" + s


def kind_guarded(v: V) -> str:
    # Guarded match on narrowed recursive union: exercises the
    # _gen_match_guarded_union codegen path (switch+guard), which must
    # also use the wrapper struct's full variant arity, not the narrowed
    # subset's member count.
    if v is None:
        return "null"
    match v:
        case bool() as b if b:
            return "bool-true"
        case bool():
            return "bool-false"
        case int() as n if n > 0:
            return "int-pos"
        case int():
            return "int-nonpos"
        case str() as s:
            return "str/" + s
        case list() as items:
            return "list/" + str(len(items))
        case dict() as d:
            return "dict/" + str(len(d))


def main() -> None:
    items: list[V] = [None, True, 42, "hi", [1, None, 2], {"k": 1, "n": None}]
    for x in items:
        print(kind(x))

    d = make_dict()
    print(kind(d))

    # is None narrowing
    a: V = None
    b: V = 7
    print(is_null(a))
    print(is_null(b))

    # Narrow-then-match: same input set as `kind` above.
    for y in items:
        print(kind_after_null_guard(y))

    # Guarded narrow-then-match
    guarded_items: list[V] = [None, True, False, 5, -3, "hi", [1, 2], {"k": 1}]
    for z in guarded_items:
        print(kind_guarded(z))

    # Non-recursive multi-member union with None
    flat_items: list[W] = [None, 7, "hi"]
    for w in flat_items:
        print(flat_kind(w))


main()
