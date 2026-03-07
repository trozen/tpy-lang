# Set membership: in and not in operators
from tpy import Int32

def main() -> None:
    s: set[Int32] = {1, 2, 3}
    print(1 in s)
    print(4 in s)
    print(1 not in s)
    print(4 not in s)

    # String set
    words: set[str] = {"hello", "world"}
    print("hello" in words)
    print("foo" in words)
    print("foo" not in words)

main()
