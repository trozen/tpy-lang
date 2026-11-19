# Comprehension shapes MIR does not lower yet, one row each
# (BUGS.md#mir-comprehension-unlowered-shapes): sema's warning is the only one.
from tpy import int32, Own, own_iter
from fixtures import grow


def grow_pairs(ps: list[tuple[int32, int32]]) -> bool:
    ps.append((7, 7))
    return True


def key_first(ss: Own[list[str]], xs: list[int32], big: bool) -> int32:  # tpyc: mir(uncovered /unsupported metadata/)
    # a dict comprehension whose value moves the owned loop target
    return len({len(s): s for s in own_iter(ss) if big and grow(xs)})  # tpyc: ok


def unpack_head(ps: list[tuple[int32, int32]], big: bool) -> int32:  # tpyc: mir(uncovered /unsupported native container element/)
    # an unpack head over tuple elements
    return len([a + b for a, b in ps if big and grow_pairs(ps)])  # tpyc: warning(/Passing borrowed container 'ps' to non-readonly parameter/)


def printed(xs: list[int32], big: bool) -> None:  # tpyc: mir(uncovered /print argument needs a scalar leaf/)
    # a comprehension printed whole
    print("printed:", [v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def array_comp(xs: list[int32], big: bool) -> int32:  # tpyc: mir(uncovered /unsupported record initializer/)
    # a comprehension of known size, an Array
    a = [big and grow(xs) for i in range(3)]  # tpyc: ok
    return len(a)


def walrus_pre(xs: list[int32], big: bool) -> int32:  # tpyc: mir(uncovered /unsupported metadata/)
    # a walrus target the enclosing body pre-declares
    ys = [y for v in xs if big and (y := v + 1) > 0]  # tpyc: ok
    return len(ys)


def in_try(xs: list[int32], big: bool) -> int32:  # tpyc: mir(uncovered /unsupported statement/)
    try:
        # inside a try body
        return len([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    except ValueError:
        return 0


def in_with(xs: list[int32], big: bool) -> int32:  # tpyc: mir(uncovered /unsupported statement/)
    with open("in_with.txt", "w") as fh:
        # inside a with body
        return len([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def in_match(xs: list[int32], k: int32, big: bool) -> int32:  # tpyc: mir(uncovered /unsupported statement/)
    match k:
        case 1:
            # inside a match arm
            return len([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
        case _:
            return 0


def main() -> None:
    print("key_first:", key_first(["a", "bb"], [1], False))
    print("unpack_head:", unpack_head([(1, 2)], False))
    printed([1, 2], False)
    print("array_comp:", array_comp([1], False))
    print("walrus_pre:", walrus_pre([1, 2], True))
    print("in_try:", in_try([1, 2], False))
    print("in_with:", in_with([1, 2], False))
    print("in_match:", in_match([1, 2], 1, False))
