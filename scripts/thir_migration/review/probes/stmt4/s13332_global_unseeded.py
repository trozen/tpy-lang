from tpy import Int32
message = b'before'
def rejected(n: Int32) -> Int32:
    global message
    return n
def main() -> None:
    print(rejected(1))
main()
