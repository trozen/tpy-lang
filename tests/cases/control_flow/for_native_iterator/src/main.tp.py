from tpy import Int32, NativeIterator

def sum_iter(it: NativeIterator[Int32]) -> Int32:
    total: Int32 = 0
    for x in it:
        total += x
    return total

def count_iter(it: NativeIterator[Int32]) -> Int32:
    n: Int32 = 0
    for x in it:
        n += 1
    return n

def first_or_fallback(it: NativeIterator[Int32], fallback: Int32) -> Int32:
    for x in it:
        return x
    return fallback

# Pass Range objects (which extend NativeIterator[Int32])
print(sum_iter(range(5)))          # 0+1+2+3+4 = 10
print(sum_iter(range(1, 6)))       # 1+2+3+4+5 = 15
print(sum_iter(range(0, 10, 3)))   # 0+3+6+9 = 18

print(count_iter(range(7)))        # 7
print(count_iter(range(0, 0)))     # 0 (empty range)

print(first_or_fallback(range(3), -1))   # 0
print(first_or_fallback(range(0), -1))   # -1 (empty range, returns fallback)
