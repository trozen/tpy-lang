# copy() of a literal-seeded container: the argument's list-vs-Array type is
# still pending when copy() is analyzed, and codegen must read it through the
# resolver. The copies are mutated after the boundary so a silent alias would
# change the source's length.
from tpy import copy, Int32


def literal_list() -> None:
    xs = [1, 2]
    ys = copy(xs)  # the copy's type resolves with the literal's
    ys.append(3)
    print(len(xs), len(ys))


def literal_dict() -> None:
    d = {"a": 1}
    e = copy(d)
    e["b"] = 2
    print(len(d), len(e))


def literal_set() -> None:
    s = {1, 2}
    t = copy(s)
    t.add(3)
    print(len(s), len(t))


def annotated_list() -> None:
    xs: list[Int32] = [1, 2]  # already resolved: the inverse of the literal case
    ys = copy(xs)
    ys.append(3)
    print(len(xs), len(ys))


def main() -> None:
    literal_list()
    literal_dict()
    literal_set()
    annotated_list()


main()
