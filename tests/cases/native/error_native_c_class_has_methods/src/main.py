from tpy.extern import native
from tpy import int32

@native(binding="C")
class Point:  # tpyc: error(/must have '...' body/)
    x: int32
    y: int32
    def distance(self) -> int32:
        return self.x

def main() -> None:
    pass
