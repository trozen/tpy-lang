from tpy.extern import native
from tpy import Int32

@native(binding="C")
class Point:  # tpyc: error(/must have '...' body/)
    x: Int32
    y: Int32
    def distance(self) -> Int32:
        return self.x

def main() -> None:
    pass
