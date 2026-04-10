# Error: nested enum from_value requires an integer argument
from enum import Enum, auto

class Message:
    class Kind(Enum):
        TEXT = auto()

    kind: Kind

def main() -> None:
    k = Message.Kind("hello")  # tpyc: error(/expected an integer type/)

main()
