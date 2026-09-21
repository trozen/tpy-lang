# A record in a second module, for the module-qualified constructor spelling.
from tpy import int32


class Tock:
    def __init__(self, n: int32 = 0) -> None:
        self.n = n
        print("  tock", n)
