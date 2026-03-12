# Error: @readonly_alt is not valid on __init__.
from tpy import Int32, readonly_alt

class Bad:
    x: Int32

    @readonly_alt  # tpyc: error(/@readonly_alt is not valid on '__init__'/)
    def __init__(self) -> None:
        self.x = Int32(0)

def main() -> None:
    pass
