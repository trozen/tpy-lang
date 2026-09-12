# Reading a class constant through an `Optional[C]` receiver matches CPython's
# AttributeError-on-None semantics: when sema can't prove the receiver is
# non-None, codegen emits a runtime null check before yielding the qualified
# constant and warns the user. When the receiver is statically narrowed
# (assignment of a non-None value, or `if x is not None` guard), both the
# warning and the runtime check are elided.
from typing import Final, Optional
from tpy import int32


class C:
    LIMIT: Final[int32] = 10

    def __init__(self) -> None:
        pass


def use(c: Optional[C]) -> None:
    print(c.LIMIT)  # tpyc: warning(/Potential None access/)
    if c is not None:
        # Narrowing silences the warning and elides the runtime check.
        print(c.LIMIT)  # tpyc: ok


def main() -> None:
    # Assignment of `C()` narrows `c` to `C` at the access -- no warning,
    # no null check, just `C::LIMIT`.
    c: Optional[C] = C()
    print(c.LIMIT)  # tpyc: ok
    # Function parameter where sema can't prove non-None.
    use(C())


main()
