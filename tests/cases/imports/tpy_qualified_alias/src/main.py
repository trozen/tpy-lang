# Test qualified tpy access with alias (import tpy as tp)
import tpy as tp

def add(a: tp.Int32, b: tp.Int32) -> tp.Int32:
    return a + b

def main():
    x: tp.Int32 = tp.Int32(5)
    y: tp.Int32 = tp.Int32(7)
    print(add(x, y))

main()
