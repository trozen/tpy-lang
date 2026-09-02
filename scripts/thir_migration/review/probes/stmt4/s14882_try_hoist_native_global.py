from tpy.extern import native_global
from tpy import Int32
score: Int32 = native_global("engine::score")
def probe(n: Int32) -> Int32:
    if n < 0:
        raise ValueError('neg')
    return n
def f(n: Int32) -> Int32:
    try:
        score = probe(n)
    except ValueError:
        return -1
    return score
def main() -> None:
    print(f(1))
main()
