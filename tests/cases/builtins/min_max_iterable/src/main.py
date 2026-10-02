# max() / min() over one iterable argument: a list, a generator, a
# comprehension, with and without key=. The first of equal elements wins and
# an empty iterable raises ValueError, as in CPython. Elements here are value
# types (numbers, str, tuples).
from typing import Iterator


def squares(n: int) -> Iterator[int]:
    for i in range(n):
        yield i * i


def peaks(rows: list[list[int]]) -> Iterator[int]:
    # generator body
    for r in rows:
        yield max(r)  # tpyc: ok


class Scores:
    values: list[float]

    def __init__(self) -> None:
        self.values = [4.5, 9.25, 0.5]

    def spread(self) -> float:
        # method: a list field as the iterable
        return max(self.values) - min(self.values)  # tpyc: ok


def best_of(words: list[str]) -> str:
    # parameter: the longest word; the first of equal keys wins
    return max(words, key=lambda w: len(w))  # tpyc: ok


def main() -> None:
    xs = [3, 1, 2]
    # list local
    print("list:", max(xs), min(xs))  # tpyc: ok
    # literal lists of other element types
    print("types:", max([1.5, 0.5]), min(["b", "a", "c"]), max([(2, "a"), (2, "b")]))  # tpyc: ok
    # generator, comprehension and generator-expression sources
    print("generator:", max(squares(5)), min(squares(5)))  # tpyc: ok
    print("comprehension:", max([x * 2 for x in xs]), min([x - 5 for x in xs]))  # tpyc: ok
    words = ["bb", "a", "ccc"]
    print("genexpr:", max(x * x for x in xs), min(x - 5 for x in xs), max(w.upper() for w in words))  # tpyc: ok
    print("genexpr_key:", max((x for x in xs), key=lambda v: -v), min((str(x) for x in xs), key=lambda s: s))  # tpyc: ok
    # key=
    print("key:", max(xs, key=lambda v: -v), min(["bb", "a", "ccc"], key=lambda s: len(s)))  # tpyc: ok
    print("key_first_wins:", best_of(["aa", "bb", "c"]), min(["aa", "bb", "c"], key=lambda w: len(w) % 2))
    # the scalar forms are unchanged
    print("scalars:", max(1, 2), min(1.5, 2.5, 0.5))
    print("method:", Scores().spread())
    rows: list[list[int]] = [[1, 5], [7, 2]]
    # inside a comprehension
    print("in_comprehension:", [max(r) for r in rows], [min(r, key=lambda v: -v) for r in rows])  # tpyc: ok
    print("generator_body:", list(peaks(rows)))
    # empty iterable
    try:
        print(max([x for x in xs if x > 10]))  # tpyc: ok
    except ValueError:
        print("empty: ValueError")
    try:
        print(min([x for x in xs if x > 10], key=lambda v: -v))
    except ValueError:
        print("empty_key: ValueError")


main()

LEVELS = [3, 1, 2]
# module level
print("module:", max(LEVELS), min(LEVELS, key=lambda v: -v))  # tpyc: ok
