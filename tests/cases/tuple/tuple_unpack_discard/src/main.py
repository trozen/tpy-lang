# Tuple unpacking with _ discard
from tpy import int32

def get_triple() -> tuple[int32, str, bool]:
    return (int32(42), "hello", True)

def main() -> None:
    _, b, _ = get_triple()
    print(b)

    a, _, c = get_triple()
    print(a)
    print(c)

main()
