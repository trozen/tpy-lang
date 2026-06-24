# itertools: count, repeat, cycle, islice, takewhile, dropwhile, filterfalse
# -- standalone, composed (islice bounding the infinite count/repeat/cycle), and
# consuming a generator source -- byte-compared against real CPython itertools.
import itertools
from tpy import Int32


def main() -> None:
    # count: default step, then custom start/step -- bounded by islice
    print([x for x in itertools.islice(itertools.count(), 4)])
    print([x for x in itertools.islice(itertools.count(10, 5), 3)])

    # repeat: bounded, empty (count 0), negative (empty, like CPython), unbounded
    print([x for x in itertools.repeat(7, 3)])
    print([x for x in itertools.repeat(7, 0)])
    print([x for x in itertools.repeat(7, -3)])
    print([x for x in itertools.islice(itertools.repeat(9), 4)])
    # the `object=` / `times=` keyword surface, matching CPython's param names
    print([x for x in itertools.repeat(object=8, times=2)])

    # cycle: infinite, sliced; and an empty source (yields nothing)
    print([x for x in itertools.islice(itertools.cycle([1, 2, 3]), 7)])
    empty: list[Int32] = []
    print([x for x in itertools.islice(itertools.cycle(empty), 5)])

    # islice over a plain list: normal, stop past the end, stop 0
    print([x for x in itertools.islice([10, 20, 30, 40, 50], 3)])
    print([x for x in itertools.islice([10, 20], 5)])
    print([x for x in itertools.islice([10, 20, 30], 0)])

    # takewhile / dropwhile / filterfalse over a list
    nums: list[Int32] = [1, 2, 3, 4, 1, 2]
    print([x for x in itertools.takewhile(lambda n: n < 3, nums)])
    print([x for x in itertools.dropwhile(lambda n: n < 3, nums)])
    print([x for x in itertools.filterfalse(lambda n: n % 2 == 0, nums)])

    # takewhile that never stops; dropwhile that drops everything
    print([x for x in itertools.takewhile(lambda n: n < 100, nums)])
    print([x for x in itertools.dropwhile(lambda n: n < 100, nums)])

    # the predicate functions consuming a *generator* source (self-iterator path)
    print([x for x in itertools.takewhile(lambda n: n < 5, itertools.count())])
    print([x for x in itertools.filterfalse(lambda n: n % 2 == 0,
                                            itertools.islice(itertools.count(), 8))])


main()
