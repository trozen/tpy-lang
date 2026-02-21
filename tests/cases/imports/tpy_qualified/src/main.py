# Test qualified tpy type access in annotations (tpy.Int32, tpy.Char)
import tpy

def add(a: tpy.Int32, b: tpy.Int32) -> tpy.Int32:
    return a + b

def main():
    x: tpy.Int32 = tpy.Int32(10)
    y: tpy.Int32 = tpy.Int32(20)
    print(add(x, y))

main()
