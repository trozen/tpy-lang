# Error: wrong argument count for __call__
from tpy import int32

class Doubler:
    def __call__(self, x: int32) -> int32:
        return x * 2

def main():
    d = Doubler()
    d(1, 2)  # tpyc: error(/expects 1/)

main()
