# Consuming set via Iterable[Own[T]] constructor at last use.
# list(set) and set(list) use own_iter_set/own_iter when source is at last use.
from tpy import Int32

def main() -> None:
    # list from set at last use
    s: set[Int32] = {10, 20, 30}
    items = list(s)
    print(items)

    # set from list at last use
    nums: list[Int32] = [1, 2, 3]
    s2 = set(nums)
    print(s2)

main()
