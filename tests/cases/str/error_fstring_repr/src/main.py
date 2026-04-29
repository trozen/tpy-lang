# Test error: f-string !r conversion on a record without __repr__ method.
# Formattable primitives (int, float, str etc.) reach __repr__ via the
# runtime format-fallback overload; the type-level rejection here is for
# records that have no __repr__ method and aren't std::formattable.
class Bare:
    pass


def main() -> None:
    x = Bare()
    s: str = f"{x!r}"  # tpyc: error(/!r conversion/)
    print(s)


main()
