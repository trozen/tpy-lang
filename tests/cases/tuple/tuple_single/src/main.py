# Single-element tuple with trailing comma in print output
from tpy import int32

def main() -> None:
    single = (int32(42),)
    print(single)
    print(single[0])

    single_str = ("only",)
    print(single_str)

main()
