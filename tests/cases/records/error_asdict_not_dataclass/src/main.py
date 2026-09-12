# Error: asdict() on a non-dataclass type
from dataclasses import asdict
from tpy import int32

class NotDataclass:
    x: int32

    def __init__(self, x: int32):
        self.x = x

def main() -> None:
    obj = NotDataclass(int32(1))
    d = asdict(obj)  # tpyc: error(/asdict\(\) requires a @dataclass instance/)

main()
