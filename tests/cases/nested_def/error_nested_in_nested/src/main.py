# Test error: nested def inside nested def
from tpy import Int32

def main() -> None:
    def outer() -> Int32:
        def inner() -> Int32:  # tpyc: error(/cannot contain further nested/)
            return 1
        return inner()
    print(outer())

main()
