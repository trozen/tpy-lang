# A `while isinstance(t, T)` whose body rebinds t off T must re-check each
# iteration -- regression: the entry-narrowing fold once made it `while (true)`.
from tpy import Own


class A:
    items: list[int]

    def __init__(self, items: Own[list[int]]) -> None:
        self.items = items


class B:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label


def drain(seed: Own[list[int]]) -> int:
    # entry-narrowed to A, then rebound off A in the body -> must exit.
    t: A | B = A(seed)
    total = 0
    while isinstance(t, A):
        total += len(t.items)
        t = B("done")
    return total


def invariant(seed: Own[list[int]]) -> int:
    # entry-narrowed to A, never rebound -> fold stays valid; break exits.
    t: A | B = A(seed)
    n = 0
    while isinstance(t, A):
        n += len(t.items)
        break
    return n


def compound(seed: Own[list[int]], flag: bool) -> int:
    # isinstance under `and`, subject rebound -> the nested fold must drop too.
    t: A | B = A(seed)
    total = 0
    while isinstance(t, A) and flag:
        total += len(t.items)
        t = B("x")
    return total


def or_rebind() -> int:
    # isinstance under `or`, subject rebound -> the `||` arm's fold must drop.
    t: A | B = A([1])
    keep = True
    n = 0
    while isinstance(t, A) or keep:
        n += 1
        t = B("x")
        keep = False
    return n


def not_rebind() -> int:
    # isinstance under `not`, subject rebound -> the `!` operand's fold must drop.
    t: A | B = A([1, 2])
    n = 0
    while not isinstance(t, B):
        n += 1
        t = B("x")
    return n


def main() -> None:
    print(drain([1, 2, 3]))
    print(invariant([7, 8]))
    print(compound([4, 5], True))
    print(compound([4, 5], False))
    print(or_rebind())
    print(not_rebind())


main()
