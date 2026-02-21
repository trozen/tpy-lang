# readonly[list[T]] parameter hint allows list() and [] to infer element type.
from tpy import Int32, readonly

def f(l: readonly[list[Int32]]) -> None:
    print(len(l))

def main() -> None:
    f(list())
    f([])

main()
