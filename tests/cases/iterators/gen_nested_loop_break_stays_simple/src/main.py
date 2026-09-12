# A break inside a NESTED loop binds to that inner loop, not the generator's,
# so a single-yield generator with one stays on the simple peephole.
from tpy import int32
from typing import Iterator


def sums(items: list[int32]) -> Iterator[int32]:
    for x in items:
        acc: int32 = 0
        for j in range(x):
            if j >= 2:
                break
            acc += j
        yield acc


def main() -> None:
    for v in sums([3, 1, 4]):
        print(v)


main()
