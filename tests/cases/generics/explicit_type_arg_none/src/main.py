# Generic function instantiated with explicit [None] type arg. Tests
# the resolve_refs._walk_type_args fix: call-site `f[None](...)` is a
# type-arg position, so None must lower to the unit type (not VoidType)
# so the substituted parameter / return slots are well-formed C++.
def identity[T](x: T) -> T:
    return x


def main() -> None:
    # Discard the result -- the call-site type-arg resolution path is
    # what this case exercises; binding the result to a typed local is
    # covered by `cases/generics/none_annotation_positions`.
    identity[None](None)
    print("ran")


main()
