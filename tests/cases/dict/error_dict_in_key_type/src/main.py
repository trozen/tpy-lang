# Wrong key type in dict 'in' operator should error
from tpy import Int32

def main() -> None:
    d = {"x": 1}
    print(Int32(5) in d)  # tpyc: error(/expected str/)

main()
