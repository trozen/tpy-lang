# Iterating a bytes-RETURNING call: the value position admits the call, but the
# for-each arm has no owned-vs-view capture shape for it, so this rejects.
from tpy import Int32


def make() -> bytes:
    return b"ab"


def count() -> Int32:
    n = 0
    for x in make():  # tpyc: error(/iter.call_shape/)
        n = n + 1
    return n


def main() -> None:
    print(count())


main()
