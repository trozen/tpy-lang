from tpy import Int32

x = 2_147_483_648  # tpyc: warning(/outside default Int32 range/)
x = Int32(1)
print(x)
