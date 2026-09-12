from tpy import int32

def sum_list(nums: list[int32]) -> int32:
    total: int32 = 0
    i: int32 = 0
    while i < len(nums):
        total += nums[i]
        i += 1
    return total

mem: list[int32] = [0] * 10
mem[0] = 42
mem[1] = 8
print(sum_list(mem))
print(len(mem))

# Unannotated list repetition (infers default int)
data = [0] * 5
data[0] = 100
print(data[0])

# chr() with BigInt element from list
chars = [72, 73]  # 'H', 'I'
print(chr(chars[0]), end='')
print(chr(chars[1]))
