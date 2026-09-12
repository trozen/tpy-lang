# Tuple-unpack and pass-through comprehensions over a param-typed Array source
# use random-access indexing inside the array_from_index lambda.
from tpy import int32, Array


def double_all(xs: Array[int32, 4]) -> None:
    ys = [x * 2 for x in xs]  # tpyc: type(/Array\[int32, 4\]/)
    print(ys[0], ys[3])


def pass_through(xs: Array[int32, 4]) -> None:
    same = [x for x in xs]  # tpyc: type(/Array\[int32, 4\]/)
    print(same[1], same[2])


def sum_pairs(ps: Array[tuple[int32, int32], 3]) -> None:
    sums = [a + b for a, b in ps]  # tpyc: type(/Array\[int32, 3\]/)
    print(sums[0], sums[2])


def main():
    xs = [1, 2, 3, 4]
    double_all(xs)
    pass_through(xs)
    pairs = [(i, i * 10) for i in range(3)]
    sum_pairs(pairs)
    empty = [i for i in range(0)]
    print(len(empty))


main()
