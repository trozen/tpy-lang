# A call between the None-test and the read may rebind a value-type global
# (any callee can `global GO; GO = ...`), so the narrow fact dies at the call.
from tpy import int32

GO: int32 | None = None


def clear() -> None:
    global GO
    GO = None


def stale() -> int32:
    if GO is not None:
        clear()
        return GO  # tpyc: error(/expected int32, got int32 \| None/)
    return -1


def main() -> None:
    print(stale())


main()
