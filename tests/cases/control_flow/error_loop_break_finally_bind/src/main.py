# A stated conservatism, pinned so a later fix is visible: the break edge is
# snapshotted AT the `break`, so a name the `finally` binds on the way out is
# missing from it and this valid program is rejected
# (`BUGS.md#break-join-ignores-finally-binding`). CPython runs the `finally` on
# the break path and prints 9. When that entry is fixed this case flips to a
# happy section in `loop_body_local_provable`.
from tpy import int32


def probe(flag: bool) -> int32:
    for i in range(3):
        try:
            if flag:
                break
        finally:
            w = 9
    return w  # tpyc: error(/variable 'w' may not be assigned at this point/)


def main() -> None:
    print(probe(True))


main()
