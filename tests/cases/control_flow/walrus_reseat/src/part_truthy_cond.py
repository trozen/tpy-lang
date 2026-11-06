# The rebinding walrus in truth tests and where it may not run: `and` /
# `or`, ternary, chained comparison, `assert` message, `match` guard,
# membership tuple, comprehension.
from tpy import int32

from fixtures import T, size, take_list


def value_opt_truthy(k: int32) -> None:
    x: int32 | None = None
    # A value Optional's truth test on the walrus's read.
    if (x := k):  # tpyc: warning(/Truthiness check on optional value/)
        print("value_opt_truthy", "yes", x)
    else:
        print("value_opt_truthy", "no", x)
    m: int32 | None = 3
    if m is not None:
        # The narrowed form.
        if (m := 0):  # tpyc: warning(/Truthiness check on optional value/)
            print("value_opt_truthy", "t", m)
        else:
            print("value_opt_truthy", "f", m)


def truthy(b: T, ob: T | None) -> None:
    x = T(5)
    # A record's `__bool__` tested on the walrus's read.
    if (x := b):  # tpyc: ok
        x.n += 10
    print("truthy", b.n)
    o: T | None = None
    # An Optional record's: None is falsy, then `__bool__`.
    if (o := ob):  # tpyc: ok
        o.n += 10
    print("truthy", ob.n if ob is not None else -1)


def maybe_walrus(k: int32) -> None:
    x = [1, 2]
    # A walrus that may not run does not end `x`'s previous value: the
    # earlier pass copies it.
    take_list(x)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    r = k > 5 and size((x := [3])) == 1  # tpyc: ok
    print("maybe_walrus", "and", len(x), r)
    take_list(x)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    n = size((x := [3])) if k > 5 else 0  # tpyc: ok
    print("maybe_walrus", "ternary", len(x), n)
    take_list(x)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    c = 0 < k < size((x := [3]))  # tpyc: ok
    print("maybe_walrus", "chain", len(x), c)


def assert_message(c: bool) -> None:
    x = [1, 2]
    # An assert's message runs only when its test fails, so its walrus does
    # not end the list the earlier pass reads: that pass copies.
    take_list(x)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    assert c, str(size((x := [3])))  # tpyc: ok
    print("assert_message", len(x))


def comprehension_walrus() -> None:
    s = "a"
    # A walrus in a comprehension's per-item part rebinds the enclosing
    # local once per item.
    xs = [(s := s + "!") for _ in range(3)]  # tpyc: ok
    print("comprehension_walrus", s, xs)


def aug_membership(c: int32) -> None:
    x = 0
    d: list[int32] = [10, 20]
    # An augmented subscript's key holds a membership test whose later
    # element is a walrus: the key is evaluated once, the walrus with it.
    d[int(c in (1, (x := 2)))] += 1  # tpyc: ok
    print("aug_membership", x, d[0], d[1])


def guard_walrus(c: int32) -> None:
    x = [1, 2]
    # A match guard runs only when its pattern matches: the earlier pass
    # copies, as before `c > 5 and ...`.
    take_list(x)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    match c:
        case 1 if size((x := [3])) > 9:  # tpyc: ok
            pass
        case _:
            pass
    print("guard_walrus", len(x))


def membership_walrus(c: int32) -> None:
    x = [1, 2]
    # A later element of a membership tuple is compared only after the
    # earlier ones: the earlier pass copies. The section avoids a value that
    # short-circuits (BUGS.md#tuple-literal-membership-needle-and-order).
    take_list(x)  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print("membership_walrus", c in (1, size((x := [3]))))  # tpyc: ok
    print("membership_walrus", len(x))


def main() -> None:
    truthy(T(1), T(2))
    truthy(T(0), None)
    maybe_walrus(2)
    assert_message(True)
    value_opt_truthy(0)
    value_opt_truthy(2)
    comprehension_walrus()
    aug_membership(1)
    guard_walrus(0)
    membership_walrus(2)
