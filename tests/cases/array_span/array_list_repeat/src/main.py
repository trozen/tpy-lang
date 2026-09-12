# Test initializing Array[T, N] with list repeat expression [val] * N
from tpy import Array, int32, int16

def test_basic() -> None:
    buf: Array[int16, 5] = [0] * 5
    print(len(buf))
    print(buf[0], buf[4])

def test_nonzero() -> None:
    arr: Array[int32, 4] = [42] * 4
    print(arr[0], arr[1], arr[2], arr[3])

def test_multi_element() -> None:
    arr: Array[int32, 6] = [1, 2, 3] * 2
    print(arr[0], arr[1], arr[2], arr[3], arr[4], arr[5])

test_basic()
test_nonzero()
test_multi_element()
