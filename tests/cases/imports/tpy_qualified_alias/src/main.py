# Test qualified tpy access with alias (import tpy as tp)
import tpy as tp

def add(a: tp.int32, b: tp.int32) -> tp.int32:
    return a + b

def main():
    x: tp.int32 = tp.int32(5)
    y: tp.int32 = tp.int32(7)
    print(add(x, y))

main()
