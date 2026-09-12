# Error: recursive nested functions are not supported
from tpy import int32

def main() -> None:
    def factorial(n: int32) -> int32:
        if n <= 1:
            return 1
        return n * factorial(n - 1)  # tpyc: error(/Recursive nested functions/)
    print(factorial(5))

main()
