# Reassigning an int32-default variable with an out-of-range literal
# promotes it to BigInt and emits a warning.
x = 0
x = 2_147_483_648  # tpyc: warning(/outside default int32 range/)
print(x)
