# User-defined generic function with T: Default bound
from tpy import int32, Own, Default, make_default

def create_default[T: Default]() -> T:
    return make_default()

def fill[T: Default](n: int32) -> Own[list[T]]:
    result: list[T] = []
    for i in range(n):
        result.append(make_default())
    return result

def main() -> None:
    x = create_default[int32]()
    print(x)

    s = create_default[str]()
    print(len(s))

    nums = fill[int32](3)
    print(len(nums))
    for v in nums:
        print(v)

main()
