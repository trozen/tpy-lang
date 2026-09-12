# T -> Optional[T] coercion: passing non-None values to Optional params.
from tpy import int32

def show(x: int32 | None) -> None:
    if x is not None:
        print(x)
    else:
        print("None")

def main() -> None:
    show(42)
    show(None)
    n: int32 = 10
    show(n)

main()
