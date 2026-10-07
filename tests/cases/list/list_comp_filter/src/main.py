# List comprehension: filter with if clause
from tpy import int32

built: list[int32] = []


class Probe:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n
        built.append(n)


def keep(p: Probe) -> bool:
    return p.n > 0


def main() -> None:
    # Filter: keep only positive
    data: list[int32] = [3, -1, 4, -2, 5]
    pos = [x for x in data if x > 0]
    print(pos)

    # Filter with transformation
    evens = [x * x for x in range(10) if x % 2 == 0]
    print(evens)

    # Multiple conditions (all must be true)
    result = [x for x in range(20) if x % 2 == 0 if x % 3 == 0]
    print(result)

    # Filter strings by length
    words: list[str] = ["hi", "hello", "hey", "howdy", "yo"]
    short = [w for w in words if len(w) <= 3]
    print(short)

    # A later filter runs only when the earlier ones passed: a Probe is built
    # for 4 and 5 alone, not for every element.
    kept = [x for x in data if x > 3 if keep(Probe(x))]  # tpyc: ok
    print("two_filters:", kept, built)

main()
