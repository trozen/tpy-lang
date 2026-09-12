# del lst[i] panics on out-of-bounds index
from tpy import int32

def main() -> None:
    items: list[int32] = [1, 2, 3]
    del items[5]

main()
