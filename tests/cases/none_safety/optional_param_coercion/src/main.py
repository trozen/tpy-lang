# T -> Optional[T] coercion: passing non-None values to Optional params.
from tpy import Int32

def show(x: Int32 | None) -> None:
    if x is not None:
        print(x)
    else:
        print("None")

def main() -> None:
    show(42)
    show(None)
    n: Int32 = 10
    show(n)

main()
