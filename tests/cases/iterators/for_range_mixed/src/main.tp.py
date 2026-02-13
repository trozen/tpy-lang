from tpy import Int32

# Mixed Int32/BigInt args should widen to BigInt (not truncate to Int32)
start: Int32 = 0
big_end = 1 << 40

# 1. Int32 start, BigInt stop
count: Int32 = 0
for i in range(start, big_end):
    count += 1
    if count >= 5:
        break
print(count)

# 2. BigInt start, Int32 stop — stop widened to BigInt
end: Int32 = 5
big_start = 1 << 40
for i in range(big_start, big_start + end):
    print(i)
