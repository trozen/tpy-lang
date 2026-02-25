# Bool mixed with concrete numeric is an error
from tpy import Int32

def main() -> None:
    x = True
    x = Int32(1)  # tpyc: error(/Type mismatch/)
    print(x)

main()
