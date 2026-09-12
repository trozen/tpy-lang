# The narrow-keeping shapes around the global call-kill: a local bind of
# the global survives calls, and a local SHADOWING the global keeps its
# own narrow across a call.
from tpy import int32

GO: int32 | None = None


def clear() -> None:
    global GO
    GO = None


def enable() -> None:
    global GO
    GO = 5


def local_bind() -> int32:
    v = GO
    if v is not None:
        clear()
        return v
    return -1


def shadowed() -> int32:
    GO: int32 | None = 9
    if GO is not None:
        clear()
        return GO
    return -1


def main() -> None:
    enable()
    print(local_bind())
    print(shadowed())


main()
