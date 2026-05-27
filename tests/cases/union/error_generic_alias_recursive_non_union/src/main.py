# Non-union self-referential alias (whether direct via `list[Foo]` or
# indirect via a generic-alias substitution that re-introduces the
# alias) has no wrapper-struct codegen path and would emit ill-formed
# C++. Reject cleanly at parse-resolution.

type Pair[T] = tuple[T, T]
type Outer = Pair[Outer]  # tpyc: error(/Recursive type alias 'Outer' must use a union form/)


def main() -> None:
    pass


main()
