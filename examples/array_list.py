# ArrayList: fully user-defined generic list with static, dynamically-initialized storage.
# Elements are placement-constructed on append and explicitly destroyed on pop/clear/__del__.
from __future__ import annotations
from tpy import Int32, UInt32, Own, copy
from tpy.mem import UninitArrayStorage


class ArrayList[T, N: int]:
    _storage: UninitArrayStorage[T, N]
    _size: Int32

    def __init__(self) -> None:
        self._storage = UninitArrayStorage[T, N]()
        self._size = 0

    def __del__(self) -> None:
        # Drop all live elements so UninitArrayStorage can safely destruct
        for i in range(self._size):
            self._storage.drop(UInt32(i))

    def __copy__(self) -> Own[ArrayList[T, N]]:
        result = ArrayList[T, N]()
        for i in range(self._size):
            result.append(self._storage.load(UInt32(i)))
        return result

    def append(self, value: Own[T]) -> None:
        self._storage.init(UInt32(self._size), value)
        self._size += 1

    def pop(self) -> Own[T]:
        self._size -= 1
        return self._storage.take(UInt32(self._size))

    def pop_at(self, index: Int32) -> Own[T]:
        result = self._storage.take(UInt32(index))
        i = index
        end = self._size - 1
        while i < end:
            self._storage.init(UInt32(i), self._storage.take(UInt32(i + 1)))
            i += 1
        self._size -= 1
        return result

    def insert(self, index: Int32, value: Own[T]) -> None:
        i = self._size - 1
        while i >= index:
            self._storage.init(UInt32(i + 1), self._storage.take(UInt32(i)))
            i -= 1
        self._storage.init(UInt32(index), value)
        self._size += 1

    def index(self, value: T) -> Int32:
        for i in range(self._size):
            if self._storage.load(UInt32(i)) == value:
                return i
        return -1

    def count(self, value: T) -> Int32:
        n: Int32 = 0
        for i in range(self._size):
            if self._storage.load(UInt32(i)) == value:
                n += 1
        return n

    def remove(self, value: T) -> None:
        self.pop_at(self.index(value))

    def reverse(self) -> None:
        lo: Int32 = 0
        hi = self._size - 1
        while lo < hi:
            a = self._storage.take(UInt32(lo))
            b = self._storage.take(UInt32(hi))
            self._storage.init(UInt32(lo), b)
            self._storage.init(UInt32(hi), a)
            lo += 1
            hi -= 1

    def __len__(self) -> Int32:
        return self._size

    def __getitem__(self, index: Int32) -> T:
        return self._storage.load(UInt32(index))

    def __setitem__(self, index: Int32, value: Own[T]) -> None:
        # Drop the old value before placement-constructing the new one
        self._storage.drop(UInt32(index))
        self._storage.init(UInt32(index), value)

    # TODO: extend(other) -- blocked by INT type params not being substituted in method
    #       signatures at call sites (same limitation as generic functions with N: int)

    def clear(self) -> None:
        for i in range(self._size):
            self._storage.drop(UInt32(i))
        self._size = 0


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


def sum_ints(lst: ArrayList[Int32, 16]) -> Int32:
    total: Int32 = 0
    for i in range(len(lst)):
        total = total + lst[i]
    return total


def print_points(lst: ArrayList[Point, 8]) -> None:
    for i in range(len(lst)):
        pt = lst[i]
        print(pt.x, pt.y)


def main() -> None:
    # --- Int32 ArrayList with capacity 16 ---
    nums = ArrayList[Int32, 16]()
    print(len(nums) == 0)        # True
    print(bool(nums))            # False

    nums.append(10)
    nums.append(20)
    nums.append(30)
    nums.append(40)
    print(len(nums))              # 4
    print(nums[0])                # 10
    print(nums[3])                # 40

    nums[1] = 99
    print(nums[1])                # 99

    print(nums.pop())             # 40
    print(len(nums))              # 3

    print(sum_ints(nums))         # 10 + 99 + 30 = 139

    nums.clear()
    print(len(nums) == 0)        # True
    print(bool(nums))            # False

    # --- Copy ---
    nums.append(1)
    nums.append(2)
    clone = copy(nums)
    clone[0] = 99
    print(nums[0])                # 1 (original unchanged)
    print(clone[0])               # 99

    # --- Point ArrayList with capacity 8 ---
    pts = ArrayList[Point, 8]()
    pts.append(Point(1, 2))
    pts.append(Point(3, 4))
    pts.append(Point(5, 6))
    print(len(pts))               # 3
    print_points(pts)             # 1 2 / 3 4 / 5 6

    p = pts.pop()
    print(p.x, p.y)              # 5 6
    print(len(pts))               # 2


main()
