from tpy.extern import native
from tpy import Int32

@native
class Vec2:  # tpyc: error(/must have '...' body/)
    x: Int32
    y: Int32
    def length(self) -> Int32:
        return self.x

def main() -> None:
    pass
