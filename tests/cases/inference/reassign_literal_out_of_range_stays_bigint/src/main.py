from tpy import int32

x = 2_147_483_648  # tpyc: warning(/outside default int32 range/)
x = int32(1)
print(x)
