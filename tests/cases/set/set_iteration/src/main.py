# Set iteration with for loop
from tpy import Int32

def main() -> None:
    s: set[Int32] = {10, 20, 30}
    for x in s:
        print(x)

    # Truthiness
    empty: set[Int32] = set()
    if s:
        print("non-empty")
    if not empty:
        print("empty is falsy")

main()
