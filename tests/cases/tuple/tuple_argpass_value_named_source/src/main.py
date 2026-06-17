# A value-element tuple local passed by value still works -- guards the
# owned-tuple storage-form handling against over-triggering on value tuples.
from tpy import Int32


def make() -> tuple[Int32, Int32]:
    return (1, 2)


def consume(p: tuple[Int32, Int32]) -> Int32:
    return p[0] + p[1]


def main():
    t = make()
    print(consume(t))


main()
