# str()/repr()/f-string for Array and Span containers
from tpy import int32, Array, Span

def show_span(s: Span[int32]) -> None:
    print(str(s))
    print(f"span={s}")

def main() -> None:
    a: Array[int32, 3] = [10, 20, 30]
    print(str(a))
    print(repr(a))
    print(f"{a}")
    print(f"array={a!r}")

    show_span(a)

main()
