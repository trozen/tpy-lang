# Set iteration with for loop
from tpy import int32

def main() -> None:
    s: set[int32] = {10, 20, 30}
    for x in s:
        print(x)

    # Truthiness
    empty: set[int32] = set()
    if s:
        print("non-empty")
    if not empty:
        print("empty is falsy")

main()
