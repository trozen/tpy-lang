# Set str() conversion and f-string interpolation
from tpy import Int32

def main() -> None:
    s: set[Int32] = {1, 2, 3}
    x: str = str(s)
    print(x)
    print(f"set: {s}")

    empty: set[Int32] = set()
    print(str(empty))
    print(f"empty: {empty}")

main()
