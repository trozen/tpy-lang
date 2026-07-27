# The IntEnum contrast to enum_truthy_call: an IntEnum tests its UNDERLYING
# value, so the operand was always embedded in the `!= 0` test (never
# droppable, unlike the plain-enum always-true fold) and the result is
# value-dependent -- Prio.ZERO is falsy where every plain enum member is truthy.
from enum import IntEnum


class Prio(IntEnum):
    ZERO = 0
    HIGH = 5


calls = 0


def make(v: Prio) -> Prio:
    global calls
    calls += 1
    return v


def main() -> None:
    if make(Prio.HIGH):
        print("high:", calls)

    if make(Prio.ZERO):
        print("unreachable")
    print("zero:", calls)

    if not make(Prio.ZERO):
        print("not zero:", calls)

    while make(Prio.HIGH):
        break
    print("while:", calls)

    # Member reads are embedded with no call to lose. ZERO is the one worth
    # spelling out: an IntEnum tests its underlying value, so a zero-valued
    # member is FALSY -- where every plain-enum member is truthy whatever its
    # value (enum/enum_truthiness pins that side).
    if Prio.HIGH:
        print("member HIGH truthy")
    if Prio.ZERO:
        print("unreachable")
    else:
        print("member ZERO falsy")
    print("total:", calls)


main()
