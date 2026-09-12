# A finally mutating the returned local INDIRECTLY (alias or closure) must
# be visible in the returned object; deferral is structural, not read-based.
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self) -> None:
        self.n = 10


def via_alias() -> Own[Box]:
    b = Box()
    a = b
    try:
        return b
    finally:
        a.n += 1


def via_closure() -> Own[Box]:
    b = Box()

    def bump() -> None:
        nonlocal b
        b.n += 1

    try:
        return b
    finally:
        bump()


def via_closure_rebind() -> Own[Box]:
    # A closure REBIND keeps the eager capture: the pending return holds the
    # pre-rebind copy (nested-def rebinds have no slot model -- BUGS.md).
    b = Box()

    def swap() -> None:
        nonlocal b
        b = Box()
        b.n = 99

    try:
        return b
    finally:
        swap()


def opt_via_closure(flag: bool) -> Own[Box] | None:
    b: Box | None = None
    if flag:
        b = Box()

    def bump() -> None:
        nonlocal b
        if b is not None:
            b.n += 1

    try:
        return b
    finally:
        bump()


def closure_dels_other() -> Own[Box]:
    # Inverse of the closure-del guard: deleting a DIFFERENT nonlocal is fine.
    b = Box()
    c = Box()

    def drop_c() -> None:
        nonlocal c
        del c

    try:
        return b
    finally:
        b.n += 1
        drop_c()


def untouched() -> Own[Box]:
    b = Box()
    try:
        return b
    finally:
        print("cleanup")


def del_other_local() -> Own[Box]:
    b = Box()
    c = Box()
    try:
        return b
    finally:
        b.n += 1
        del c


def main() -> None:
    print(via_alias().n)
    print(via_closure().n)
    print(via_closure_rebind().n)
    r = opt_via_closure(True)
    if r is not None:
        print(r.n)
    print(closure_dels_other().n)
    print(untouched().n)
    print(del_other_local().n)


main()
