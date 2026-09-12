# Warning: slice assignment while iterating over the same list (invalidates iterator).
from tpy import int32

def main() -> None:
    a: list[int32] = [1, 2, 3, 4, 5]
    for x in a:
        a[0:1] = [99]  # tpyc: warning(/Mutation of 'a'.*while borrowed/)
        break

main()
