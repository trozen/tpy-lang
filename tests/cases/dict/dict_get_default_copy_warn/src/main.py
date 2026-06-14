# Two-arg dict.get on reference values warns (copies where CPython
# aliases); copy() acknowledges, value-type results stay silent.
# Copy semantics intended here: the test reads through the result only.
from tpy import Int32, copy


def main():
    d: dict[str, list[Int32]] = {}
    d["a"] = [1, 2]
    fallback: list[Int32] = []
    x = d.get("a", fallback)  # tpyc: warning(/returns a copy of the stored value/)
    print(len(x))
    y = copy(d.get("a", fallback))  # tpyc: ok
    print(len(y))
    counts: dict[str, Int32] = {}
    m = counts.get("k", 5)  # tpyc: ok
    print(m)


main()
