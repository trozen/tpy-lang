# Tuple unpack where both if/else branches assign the targets, then read
# after the if -- the targets must hoist to the enclosing scope (mirroring
# scalar definite-assignment) so the post-if read sees them.
from tpy import Int32


def pair(n: Int32) -> tuple[Int32, Int32]:
    return (n, n + 1)


def main() -> None:
    flag = len("ab") > 1
    if flag:
        a, b = pair(1)
    else:
        a, b = pair(10)
    print(a)
    print(b)


main()
