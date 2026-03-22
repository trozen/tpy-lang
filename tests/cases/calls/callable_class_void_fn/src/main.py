# Callable with non-void return passed to Fn[[...], None] (return discarded)
from tpy import Int32, Fn

class Doubler:
    def __call__(self, x: Int32) -> Int32:
        return x * 2

def apply_and_discard(f: Fn[[Int32], None], x: Int32) -> None:
    f(x)

def main():
    d = Doubler()
    apply_and_discard(d, 5)
    print("ok")

main()
