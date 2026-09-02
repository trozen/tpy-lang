from typing import TypedDict
from tpy import Int32
class TD(TypedDict):
    k: Int32
def f(xs: list[TD]) -> Int32:
    return xs[0]['k']
def main() -> None:
    print(f([TD(k=1)]))
main()
