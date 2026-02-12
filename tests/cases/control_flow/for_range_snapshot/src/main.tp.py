from tpy import Int32

# 1. Mutating stop inside loop — should iterate original count
def test_stop_snapshot() -> None:
    n: Int32 = 5
    count: Int32 = 0
    for i in range(n):
        n = 0
        count += 1
    print(count)  # 5

# 2. Mutating start/stop/step inside loop — all captured once
def test_all_args_snapshot() -> None:
    start: Int32 = 0
    stop: Int32 = 10
    step: Int32 = 2
    total: Int32 = 0
    for i in range(start, stop, step):
        start = 100
        stop = 100
        step = 100
        total += i
    print(total)  # 0 + 2 + 4 + 6 + 8 = 20

# 3. Function call in stop — evaluated once, not per-iteration
def get_stop(n: Int32) -> Int32:
    print(n)  # side effect to verify call count
    return n

count: Int32 = 0
for i in range(get_stop(3)):
    count += 1
print(count)  # 3 (get_stop prints "3" once, then loop runs 3 times)

# 4. BigInt stop mutated — .to_int32() captured once
n = 4
count2: Int32 = 0
for i in range(n):
    n = 0
    count2 += 1
print(count2)  # 4

test_stop_snapshot()
test_all_args_snapshot()
