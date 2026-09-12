# Test error: nested def inside nested def
from tpy import int32

def main() -> None:
    def outer() -> int32:
        def inner() -> int32:  # tpyc: error(/cannot contain further nested/)
            return 1
        return inner()
    print(outer())

main()
