from tpy import int32
def helper(x: int32) -> int32:
    return x + 1
def main() -> None:
    def helper(x: int32) -> int32:
        return x + 2
    print(helper(1))
main()
