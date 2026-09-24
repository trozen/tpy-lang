# A literal tuple arm beside a bound tuple names its element as `int`, so the
# two types read apart in the message (BUGS.md#literal-elem-tuple-ternary-mismatch).
from tpy import int32


def pick(f: bool) -> tuple[int32, str]:
    t = (3, "c")
    # The mixed literal / name pair: the message must not read 'X' and 'X'.
    return t if f else (4, "d")  # tpyc: error(/'tuple\[int32, str\]' and 'tuple\[int, str\]'/)


def main() -> None:
    print(pick(True))


main()
