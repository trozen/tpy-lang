# Array, whose stub declares no constructor, refuses a keyword at its
# construction as a user record with no __init__ does.
from tpy import Array, int32


def main() -> None:
    a = Array[int32, 2](x=1)  # tpyc: error(/'Array\(\)' got unexpected keyword argument 'x'/)
    print(a[0])


main()
