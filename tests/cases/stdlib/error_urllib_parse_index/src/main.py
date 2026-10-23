# The urlsplit result is a record, not a namedtuple: integer indexing is a
# compile error (the deliberate, compile-visible API divergence from CPython).
from urllib.parse import urlsplit


def main() -> None:
    p = urlsplit("http://h/path")
    print(p[0])  # tpyc: error(/Type 'SplitResult' is not subscriptable/)


main()
