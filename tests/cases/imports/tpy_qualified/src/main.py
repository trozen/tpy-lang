# Test qualified tpy type access in annotations (tpy.int32, tpy.char)
import tpy

def add(a: tpy.int32, b: tpy.int32) -> tpy.int32:
    return a + b

def main():
    x: tpy.int32 = tpy.int32(10)
    y: tpy.int32 = tpy.int32(20)
    print(add(x, y))

main()
