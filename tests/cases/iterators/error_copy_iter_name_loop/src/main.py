# A `copy_iter` handle bound to a NAME and then iterated: the name-iterable arm
# is OwnIter-only, and CopyIter binds non-consuming.
# TPy rejects the `for x in ci:` loop today.
from tpy import int32, copy_iter


def run() -> None:
    src: list[int32] = [1, 2, 3]
    ci = copy_iter(src)
    total = 0
    for x in ci:  # tpyc: error(/iter.name_family.record_nonf1/)
        total += x
    print(total, len(src))


def main() -> None:
    run()


main()
