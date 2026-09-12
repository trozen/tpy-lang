# ord() with single-char string literal is constant-folded to an int.
from tpy import int32

def main() -> None:
    # Constant-folded cases
    print(ord("A"))
    print(ord("0"))
    print(ord("\n"))
    print(ord(" "))

    # Runtime call (variable, not a literal)
    s = "B"
    print(ord(s))

    # Verify values match expected ASCII codes
    a: int32 = ord("z")
    print(a)

main()
