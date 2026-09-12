# Regression: @nocopy + __del__ field cannot be initialized in the body when
# the initializer RHS references a body-local -- MIL-hoist is blocked by the
# local ref, and the field has no default ctor (suppressed for @nocopy+__del__
# records), so there is no safe way to generate the C++. Must surface as a
# clean codegen error with actionable guidance, not as a cryptic C++ diagnostic.
from tpy import int32, nocopy


@nocopy
class Resource:
    id: int32
    def __init__(self, id: int32) -> None:
        self.id = id
    def __del__(self) -> None:
        print("drop", self.id)


def pick(seed: int32) -> int32:
    return seed + int32(100)


class Holder:
    _r: Resource
    def __init__(self, seed: int32) -> None:
        tmp = pick(seed)
        self._r = Resource(tmp)   # tpyc: error(/must be initialized before any local variable is bound/)


def main() -> None:
    h = Holder(int32(1))
    print(h._r.id)


main()
