from tpy import Int32

def sum_list(nums: list[Int32]) -> Int32:
    total: Int32 = 0
    i: Int32 = 0
    while i < len(nums):
        total += nums[i]
        i += 1
    return total

mem: list[Int32] = [0] * 10
mem[0] = 42
mem[1] = 8
print(sum_list(mem))
print(len(mem))

# Unannotated list repetition (should infer BigInt)
data = [0] * 5
data[0] = 100
print(data[0])

# chr() with BigInt element from list
chars = [72, 73]  # 'H', 'I'
print(chr(chars[0]), end='')
print(chr(chars[1]))
