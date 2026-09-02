from tpy import Int32
def helper(x: Int32) -> Int32:
    return x + 1
def main() -> None:
    def helper(x: Int32) -> Int32:
        return x + 2
    print(helper(1))
main()
