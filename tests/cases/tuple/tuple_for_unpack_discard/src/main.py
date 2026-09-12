# For-loop tuple unpacking with discard: for _, name in items
from tpy import int32

def main() -> None:
    items: list[tuple[int32, str]] = [(1, "one"), (2, "two")]
    for _, name in items:
        print(name)

main()
