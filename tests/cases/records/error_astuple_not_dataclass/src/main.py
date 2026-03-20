# Error: astuple() on a non-dataclass type
from dataclasses import astuple
from tpy import Int32

class NotDataclass:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x

def main() -> None:
    obj = NotDataclass(Int32(1))
    t = astuple(obj)  # tpyc: error(/astuple\(\) requires a @dataclass instance/)

main()
