from tpy.extern import native
from tpy import int32

@native
class Vec2:  # tpyc: error(/must have '...' body/)
    x: int32
    y: int32
    def length(self) -> int32:
        return self.x

def main() -> None:
    pass
