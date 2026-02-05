from tpy import Int32

# This print verifies D is only initialized once
print("D init")

def d_value() -> Int32:
    return Int32(5)
