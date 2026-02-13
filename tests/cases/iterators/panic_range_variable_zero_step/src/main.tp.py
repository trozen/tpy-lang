from tpy import Int32

# Variable zero-step should panic, same as literal zero-step
step: Int32 = 0
for i in range(1, 5, step):
    print(i)
