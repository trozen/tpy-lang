# A `print(file=...)` sink chosen by a ternary: each arm is an admitted sink
# read, so the select goes under the pinned as_ostream consumer whole. stderr
# output is not captured by the runner, so only the stdout arm shows up.
import sys
from tpy import int32


def emit(n: int32, to_out: bool) -> None:
    print(n, file=sys.stdout if to_out else sys.stderr)  # tpyc: ok


def main() -> None:
    emit(1, True)
    emit(2, False)
    emit(3, True)


main()
