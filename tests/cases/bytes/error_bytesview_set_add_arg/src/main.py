# A BytesView NAME argument at a `set[BytesView]` insert slot -- the view twin of
# the Own[view] name-argument boundary.
from tpy import BytesView


def collect(k: BytesView) -> None:
    s: set[BytesView] = set()
    s.add(k)  # tpyc: error(/method.arg_shape/)
    print(len(s))


def main() -> None:
    collect(b"ab")


main()
