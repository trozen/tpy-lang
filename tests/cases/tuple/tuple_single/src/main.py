# Single-element tuple with trailing comma in print output
from tpy import Int32

def main() -> None:
    single = (Int32(42),)
    print(single)
    print(single[0])

    single_str = ("only",)
    print(single_str)

main()
