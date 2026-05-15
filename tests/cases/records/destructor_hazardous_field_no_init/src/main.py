# Regression: a record with __del__ + a raw Ptr[T] field + no __init__ must
# NOT get a codegen-emitted `T() = default;`. The defaulted ctor would leave
# `_handle` indeterminate, and ~Hazard reading it would be UB.
#
# A safe-default-field sibling (Safe) still gets `Safe() = default;` -- the
# `str` field has a well-defined empty state, so default-construction is
# safe even with a __del__ that reads it. The snapshot is the regression
# guard: Safe must have `Safe() = default;`, Hazard must not.
from tpy import Int32, Ptr


# Hazardous: raw pointer field with no in-class initializer. Codegen
# suppresses Hazard()'s default ctor so default-construction is impossible.
class Hazard:
    _handle: Ptr[Int32]

    def __del__(self) -> None:
        print("dropping hazard")


# Safe sibling: str field has a well-defined empty default at the C++ level
# (std::string_view{} -> empty view). Path-2 still emits Safe() = default;.
class Safe:
    name: str

    def __del__(self) -> None:
        print("dropping safe:", self.name)


def main() -> None:
    s = Safe()  # tpyc: ok
    print("safe name empty:", s.name == "")


main()
