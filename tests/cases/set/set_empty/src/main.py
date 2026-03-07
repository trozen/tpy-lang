# Empty set with type annotation
from tpy import Int32

def main() -> None:
    s: set[Int32] = set()
    print(s)
    print(len(s))
    s.add(42)
    print(s)

main()
