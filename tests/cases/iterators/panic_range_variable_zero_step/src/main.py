from tpy import int32

# Variable zero-step should panic, same as literal zero-step
step: int32 = 0
for i in range(1, 5, step):
    print(i)
