# Empty set with type annotation
from tpy import int32

def main() -> None:
    s: set[int32] = set()
    print(s)
    print(len(s))
    s.add(42)
    print(s)

main()
