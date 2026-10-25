# A narrowing constructor of an integer constant generic checks the value:
# `uint8(M)` with `M = 300` raises OverflowError, as `uint8(v)` for an int32
# `v` does.
from tpy import uint8


class Small[M: int]:
    def run(self) -> None:
        # The checked narrowing of the int32 value of `M`.
        print("small", uint8(M))  # tpyc: ok


def main() -> None:
    Small[300]().run()


main()
