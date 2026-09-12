# Test str * n and n * str repetition operators.
from tpy import int32

def main() -> None:
    s: str = "abc"
    n: int32 = 3

    # Basic repetition
    print(s * n)
    print(s * 1)
    print(s * 0)

    # Reverse form
    print(n * s)

    # Literal repetition
    print("xy" * 4)

    # Negative count returns empty
    print(s * -1)

main()
