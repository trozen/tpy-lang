# Bool mixed with concrete numeric is an error
from tpy import int32

def main() -> None:
    x = True
    x = int32(1)  # tpyc: error(/Type mismatch/)
    print(x)

main()
