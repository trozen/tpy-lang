# A forward-referenced generator: frame borrow facts are stamped at
# registration, so the consume of xs is demoted (copy + warning).
from typing import Iterator
from tpy import int32, Own


def main():
    xs = [1, 2, 3]
    g = gen(xs)
    print(drop(xs))  # tpyc: warning(/copies/)
    for v in g:
        print(v)


def gen(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def drop(xs: Own[list[int32]]) -> int32:
    store: list[list[int32]] = []
    store.append(xs)
    return len(store)


main()
