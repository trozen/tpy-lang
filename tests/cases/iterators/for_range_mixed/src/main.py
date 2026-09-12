from tpy import int32

# Mixed int32/BigInt args should widen to BigInt (not truncate to int32)
start: int32 = 0
big_end = 1 << 40  # tpyc: warning(/outside default int32 range/)

# 1. int32 start, BigInt stop
count: int32 = 0
for i in range(start, big_end):
    count += 1
    if count >= 5:
        break
print(count)

# 2. BigInt start, int32 stop -- stop widened to BigInt
end: int32 = 5
big_start = 1 << 40  # tpyc: warning(/outside default int32 range/)
for i in range(big_start, big_start + end):
    print(i)
