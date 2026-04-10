# Nested enum inside a class, with member access and field usage.
# Uses short name (Kind) inside the class body for CPython compatibility.
from tpy import Int32
from enum import Enum, auto

class Message:
    class Kind(Enum):
        TEXT = auto()
        IMAGE = auto()
        VIDEO = auto()

    kind: Kind
    data: Int32

    def __init__(self, kind: Kind, data: Int32) -> None:
        self.kind = kind
        self.data = data

def main() -> None:
    m = Message(Message.Kind.TEXT, 42)
    print(m.kind)
    print(m.data)

    k: Message.Kind = Message.Kind.IMAGE
    print(k)
    print(k.name)
    print(k.value)

main()
