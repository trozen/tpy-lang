# Tests that containers coerce to Optional[Span[readonly[T]]] at call sites
from tpy import int32, Span, readonly

def has_values(values: Span[readonly[int32]] | None) -> bool:
    return values is not None

def main() -> None:
    arr: list[int32] = [10, 20, 30]
    print(has_values(arr))
    print(has_values(None))
    print(has_values([42, 99]))

main()
