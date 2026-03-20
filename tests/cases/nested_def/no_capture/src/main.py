# Test nested def with no captures (pure local function)
from tpy import Int32

def main() -> None:
    def double(x: Int32) -> Int32:
        return x * 2
    print(double(21))
    print(double(0))

main()
