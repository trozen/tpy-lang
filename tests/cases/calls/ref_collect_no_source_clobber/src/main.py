# list(map(identity, src)) must copy elements into the new list, not move.
# Uses str fields (non-trivial move) to catch accidental move-from-source --
# moving from a std::string leaves it empty, so src[i].name would be "".
from tpy import copy_iter

class Named:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def identity(n: Named) -> Named:
    return n

def main() -> None:
    src: list[Named] = [Named("alice"), Named("bob")]

    result = list(copy_iter(map(identity, src)))
    # Source must be intact
    print(src[0].name)
    print(src[1].name)
    # Result must have copies
    print(result[0].name)
    print(result[1].name)

main()
