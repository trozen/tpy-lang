# Phase 20 Stage 5a: sema rejects the original BUGS.md slicing shape
# at compile time -- storing a borrow of a polymorphic exception type
# into an owned slot of the same (or strict-subclass-of) type would
# silently drop the dynamic type. The fix points at Box[Throwable].
class Holder:
    exc: BaseException | None
    def __init__(self) -> None:
        self.exc = None

    def store(self, e: BaseException) -> None:
        self.exc = e  # tpyc: error(/dynamic type may be a subclass/)


def main() -> None:
    pass


main()
