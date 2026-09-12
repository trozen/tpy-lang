from tpy.extern import native_global
from tpy import int32
score: int32 = native_global("engine::score")
def probe(n: int32) -> int32:
    if n < 0:
        raise ValueError('neg')
    return n
def f(n: int32) -> int32:
    try:
        score = probe(n)
    except ValueError:
        return -1
    return score
def main() -> None:
    print(f(1))
main()
