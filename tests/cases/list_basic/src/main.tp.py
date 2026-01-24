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
