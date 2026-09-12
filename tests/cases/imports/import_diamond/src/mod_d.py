from tpy import int32

# This print verifies D is only initialized once
print("D init")

def d_value() -> int32:
    return int32(5)
