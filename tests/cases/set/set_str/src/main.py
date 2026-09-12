# Set str() conversion and f-string interpolation
from tpy import int32

def main() -> None:
    s: set[int32] = {1, 2, 3}
    x: str = str(s)
    print(x)
    print(f"set: {s}")

    empty: set[int32] = set()
    print(str(empty))
    print(f"empty: {empty}")

main()
