# Wrong key type in dict 'in' operator should error
from tpy import int32

def main() -> None:
    d = {"x": 1}
    print(int32(5) in d)  # tpyc: error(/expected str/)

main()
