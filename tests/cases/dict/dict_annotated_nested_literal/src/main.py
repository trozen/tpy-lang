# A dict literal whose VALUE is itself a container literal, at an ANNOTATED
# slot: the nested literal takes its type from the enclosing slot's
# annotation, one level at a time, so `{"a": [1, 2]}` at a
# `dict[str, list[int32]]` slot builds a real list value. The subject line of
# every section is the literal; each section then mutates the stored inner
# container through the dict and prints, so a copy at the store would show.
# The positions differ only in the variable model, since the resolution is in
# the literal analysis: free function, method, constructor, module level,
# generator, comprehension, try/finally and match arm are sectioned, plus the
# sinks a value can reach (return, argument, append, setitem). The async
# position is the generator's variable model and is not repeated; the closure
# position rejects for an unrelated reason -- the plain `list[list[int32]]`
# sibling rejects there too, per
# BUGS.md#nested-def-pending-container-unresolved.
from typing import Iterator
from tpy import int32, Array, Own


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Guard:
    def __enter__(self) -> None:
        pass

    def __exit__(self, kind, value, tb) -> None:
        pass


class Sink:
    table: dict[str, list[int32]]

    # constructor-ARGUMENT sink: the literal is resolved against the ctor's
    # param annotation, not against a variable's
    def __init__(self, table: Own[dict[str, list[int32]]]) -> None:
        self.table = table


class Reg:
    table: dict[str, list[int32]]

    def __init__(self) -> None:
        self.table = {"a": [1, 2]}  # tpyc: ok

    def grow(self) -> int32:
        local: dict[str, list[int32]] = {"m": [7]}  # tpyc: ok
        local["m"].append(8)
        return len(local["m"])


GLOBAL_ROWS: dict[str, list[int32]] = {"g": [1]}  # tpyc: ok


def build() -> Own[dict[str, list[int32]]]:
    return {"r": [1, 2]}  # tpyc: ok


def take(d: dict[str, list[int32]]) -> int32:
    d["p"].append(9)
    return len(d["p"])


def rows() -> Iterator[int32]:
    inside: dict[str, list[int32]] = {"y": [1, 2]}  # tpyc: ok
    inside["y"].append(3)
    for v in inside.values():
        yield len(v)


def main() -> None:
    # Free function: the plain local, the shape every other section varies.
    plain: dict[str, list[int32]] = {"a": [1, 2]}  # tpyc: ok
    plain["a"].append(3)
    print("plain", plain)

    # A record element inside the nested list stays a stored object.
    points: dict[str, list[Point]] = {"a": [Point(1)]}  # tpyc: ok
    points["a"][0].x = 5
    print("record", points["a"][0].x)

    # Two levels of nesting, and the sibling container kinds at the value slot.
    deep: dict[str, list[list[int32]]] = {"a": [[1, 2]]}  # tpyc: ok
    deep["a"][0].append(3)
    print("deep", deep)
    groups: dict[str, set[int32]] = {"a": {1, 2}}  # tpyc: ok
    groups["a"].add(3)
    print("set", len(groups["a"]))
    pairs: dict[str, tuple[list[int32], int32]] = {"a": ([1, 2], 3)}  # tpyc: ok
    print("tuple", pairs)
    fixed: dict[str, Array[int32, 2]] = {"a": [1, 2]}  # tpyc: ok
    print("array", fixed["a"][0], fixed["a"][1])

    # An Optional slot: the literal takes the dict member's annotation, since
    # the None alternative cannot be written as a literal.
    maybe: dict[str, list[int32]] | None = {"o": [1, 2]}  # tpyc: ok
    if maybe is not None:
        # The stored element is mutated through the narrowed receiver, so a
        # copy at the store would print the un-appended list.
        maybe["o"].append(3)
        print("optional", maybe)

    # Jagged peer values still reconcile to one list type.
    jagged: dict[str, list[int32]] = {"a": [1, 2], "b": [3]}  # tpyc: ok
    jagged["b"].append(4)
    print("jagged", jagged)

    # Constructor / field initializer, and the method position.
    r = Reg()
    r.table["a"].append(3)
    print("field", r.table, r.grow())

    # Module level.
    GLOBAL_ROWS["g"].append(2)
    print("global", GLOBAL_ROWS)

    # Return, argument, append and setitem sinks.
    built = build()
    built["r"].append(3)
    print("return", built)
    print("arg", take({"p": [1, 2]}))
    stack: list[dict[str, list[int32]]] = []
    stack.append({"a": [1, 2]})  # tpyc: ok
    stack[0]["a"].append(3)
    print("append", stack)
    nested: dict[str, dict[str, list[int32]]] = {}
    nested["k"] = {"a": [1, 2]}  # tpyc: ok
    nested["k"]["a"].append(3)
    print("setitem", nested)

    # `with` body.
    with Guard():
        in_with: dict[str, list[int32]] = {"w": [1]}  # tpyc: ok
        in_with["w"].append(2)
        print("with", in_with)

    # Constructor-argument sink.
    sink = Sink({"s": [1, 2]})  # tpyc: ok
    sink.table["s"].append(3)
    print("ctor arg", sink.table)

    # Comprehension result at an annotated slot.
    comp: dict[int32, list[int32]] = {k: [k] for k in range(2)}  # tpyc: ok
    comp[0].append(9)
    print("comp", comp)

    # Generator body.
    for n in rows():
        print("generator", n)

    # try/finally and match-arm positions.
    try:
        in_try: dict[str, list[int32]] = {"t": [1]}  # tpyc: ok
        in_try["t"].append(2)
        print("try", in_try)
    finally:
        print("finally done")
    tag = 1
    match tag:
        case 1:
            in_match: dict[str, list[int32]] = {"c": [1]}  # tpyc: ok
            in_match["c"].append(2)
            print("match", in_match)
        case _:
            print("match other")


main()
