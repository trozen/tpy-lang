# Generic function instantiated with explicit [None] type arg. Tests
# the resolve_refs._walk_type_args fix: call-site `f[None](...)` is a
# type-arg position, so None must lower to the unit type (not VoidType)
# so the substituted parameter / return slots are well-formed C++.
def identity[T](x: T) -> T:
    return x


def main() -> None:
    # Discard the result: assigning to a typed slot would force the
    # surrounding position to commit to NoneType vs VoidType, which is
    # a separate design question (variable annotation `x: None` is at
    # top-level position -> VoidType -> ill-formed C++). Calling the
    # generic and dropping the result is enough to exercise the
    # call-site type-arg resolution path.
    identity[None](None)
    print("ran")


main()
