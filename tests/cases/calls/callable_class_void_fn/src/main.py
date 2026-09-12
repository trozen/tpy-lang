# Callable with non-void return passed to Fn[[...], None] (return discarded)
from tpy import int32, Fn

class Doubler:
    def __call__(self, x: int32) -> int32:
        return x * 2

def apply_and_discard(f: Fn[[int32], None], x: int32) -> None:
    f(x)

def main():
    d = Doubler()
    apply_and_discard(d, 5)
    print("ok")

main()
