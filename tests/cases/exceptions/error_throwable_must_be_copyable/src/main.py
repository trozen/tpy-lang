# Stage 2c sema rule: classes implementing Throwable must be
# copy-constructible. The macro-emitted (and post-Stage-4 codegen-emitted)
# clone() / __raise__() do `make_unique<This>(*this)` / `throw *this`, both
# of which require a usable copy ctor.
from tplib import Box


class Resource:
    def __init__(self) -> None:
        pass
    def __del__(self) -> None:
        # __del__ implicitly deletes the copy ctor at the C++ level, so any
        # owner of `Resource` becomes non-copy-constructible.
        pass


class BadError(Exception):  # tpyc: error(/not copy-constructible/)
    handle: Box[Resource]
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.handle = Box(Resource())


def main() -> None:
    pass


main()
