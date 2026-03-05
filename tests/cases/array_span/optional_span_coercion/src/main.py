# Tests that containers coerce to Optional[Span[T]] at call sites
from tpy import Int32, ReadOnlySpan

def has_values(values: ReadOnlySpan[Int32] | None) -> bool:
    return values is not None

def main() -> None:
    arr: list[Int32] = [10, 20, 30]
    print(has_values(arr))
    print(has_values(None))
    print(has_values([42, 99]))

main()
