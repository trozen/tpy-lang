# Fields not initialized before constructor body: warning if default-constructible,
# silent if they have a class-level default. No warning for fields in init section.
from tpy import Int32

class Config:
    x: Int32
    y: Int32          # not initialized in init section -> warning at split point
    z: Int32 = Int32(0)  # has class-level default -> silent

    def __init__(self, x: Int32):
        self.x = x             # init section
        print("init")          # tpyc: warning(/y.*is not initialized before the constructor body/)

def main() -> None:
    c = Config(Int32(42))
    print(c.x)

main()
