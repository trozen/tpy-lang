# Regression guard: the T | None <-> Ptr[T] coercion (#17 + #21 fix) is
# gated on `OptionalType.uses_pointer_repr()`, which is False for value-typed
# inners (int32, bool, etc.). So `int32 | None -> Ptr[int32]` must still be
# rejected as a type mismatch -- the two have *different* C++ shapes
# (std::optional<int32_t> vs int32_t*) and the rule must not silently widen.
from tpy import Ptr, int32


def maybe_int() -> int32 | None:
    return int32(1)


def main() -> None:
    pi: Ptr[int32] = maybe_int()  # tpyc: error(/Type mismatch.*Ptr\[int32\].*int32 \| None/)
    print(pi)


main()
