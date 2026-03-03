# del lst[i] panics on out-of-bounds index
from tpy import Int32

def main() -> None:
    items: list[Int32] = [1, 2, 3]
    del items[5]

main()
