# A float subject takes the `==` chain (no C++ case label can be
# floating-point), and a guard on any arm demotes it once more onto the
# GUARDED chain -- whose arms re-read the `auto&` subject alias after each
# guard runs. At a field (or subscript) subject a guard that mutates the field
# would therefore make a later arm test the NEW value, where CPython snapshots
# the subject once. The tier stays rejected at a field subject until a scalar
# subject binds by value (BUGS.md#match-subject-alias-under-guard-mutation).


class P:
    def __init__(self, f: float) -> None:
        self.f = f


def bump(p: P) -> bool:
    p.f = 2.0
    return False


# The unguarded field subject on the same chain compiles: nothing runs between
# two arm tests, so the alias cannot change under them.
def unguarded(p: P) -> str:
    match p.f:
        case 1.0:
            return "one"
        case _:
            return "other"


def guarded(p: P) -> str:
    match p.f:  # tpyc: error(/not yet supported.*stmt\.match/)
        case 1.0 if bump(p):
            return "one"
        case 2.0:
            return "two"
        case _:
            return "other"


def main() -> None:
    p = P(1.0)
    print(unguarded(p), guarded(p))


main()
