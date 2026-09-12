# Multiple inheritance cannot cross the boundary: CPython rejects two bases
# with distinct C instance layouts, so an @export class with two record bases
# is a located error even when both bases are exposed.
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class Left:
    def lhs(self) -> int64:
        return 1


@export
class Right:
    def rhs(self) -> int64:
        return 2


@export
class Both(Left, Right):  # tpyc: error(/multiple inheritance cannot be exposed/)
    def sum(self) -> int64:
        return self.lhs() + self.rhs()
