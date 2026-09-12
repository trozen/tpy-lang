from tpy import int32
from traits import Printable

class Message:
    text: str

    def __init__(self, text: str):
        self.text = text

    def to_string(self) -> str:
        return self.text

def show(p: Printable) -> None:
    print(p.to_string())

def main() -> int32:
    m = Message("Hello")
    show(m)
    return int32(0)

main()
