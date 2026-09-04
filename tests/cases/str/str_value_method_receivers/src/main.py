# A str-family method receiver that is a VALUE rvalue rather than a name: a
# concat binop and a select. Both substitute bare into the native method, so
# there is no copy-vs-alias question at the receiver.
from tpy import Int32


def shout(a: str, b: str, c: bool) -> Int32:
    n = (a + b).upper()         # tpyc: ok -- a concat receiver
    m = (a if c else b).upper()  # tpyc: ok -- a select receiver
    print(n, m)
    return len(n) + len(m)


def main() -> None:
    print(shout("ab", "cd", True))


main()
