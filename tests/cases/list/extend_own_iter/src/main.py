# extend() with own_iter() moves elements instead of copying.
from tpy import Int32, own_iter

def main() -> None:
    a: list[Int32] = [1, 2, 3]
    b: list[Int32] = [10, 20]
    b.extend(own_iter(a))
    print(b)

main()
