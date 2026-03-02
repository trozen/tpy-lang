# Generic function with tuple type parameter
from tpy import Int32

def first_of_pair[T](p: tuple[T, T]) -> T:
    return p[0]

def swap[A, B](p: tuple[A, B]) -> tuple[B, A]:
    return (p[1], p[0])

def main() -> None:
    nums = (Int32(10), Int32(20))
    x = first_of_pair(nums)
    print(x)

    pair = (Int32(5), "five")
    swapped = swap(pair)
    print(swapped)

main()
