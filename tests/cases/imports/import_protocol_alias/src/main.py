# User-defined Protocol imported under an alias (`from X import P as Q`)
# flows through sema's `register_protocol(info, local_name)` at
# analyzer.py:2054 and the codegen concept path looks it up by the alias.
from tpy import Int32
from traits import Printable as P


class Message:
    text: str

    def __init__(self, text: str):
        self.text = text

    def to_string(self) -> str:
        return self.text


def show(p: P) -> None:
    print(p.to_string())


def main() -> Int32:
    m = Message("Hello")
    show(m)
    return Int32(0)


main()
