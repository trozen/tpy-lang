# Error: for-loop unpacking with wrong number of targets
from tpy import Int32

def main() -> None:
    items: list[tuple[Int32, str]] = [(1, "one")]
    for a, b, c in items:  # tpyc: error(/Cannot unpack tuple of 2 elements into 3 targets/)
        print(a, b, c)

main()
