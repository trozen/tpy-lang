from tpy import Int32

x = 0
x = Int32(10)
x = int(20)
print(x)

f = 0
f = Int32(3)
f = 1.5  # tpyc: error(/expected Int32, got float/)
print(f)
