# Regression guard: the T | None <-> Ptr[T] coercion (#17 + #21 fix) is
# gated on `OptionalType.uses_pointer_repr()`, which is False for value-typed
# inners (Int32, bool, etc.). So `Int32 | None -> Ptr[Int32]` must still be
# rejected as a type mismatch -- the two have *different* C++ shapes
# (std::optional<int32_t> vs int32_t*) and the rule must not silently widen.
from tpy import Ptr, Int32


def maybe_int() -> Int32 | None:
    return Int32(1)


def main() -> None:
    pi: Ptr[Int32] = maybe_int()  # tpyc: error(/Type mismatch.*Ptr\[Int32\].*Int32 \| None/)
    print(pi)


main()
