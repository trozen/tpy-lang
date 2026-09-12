from tpy import int32
message = b'before'
def rejected(n: int32) -> int32:
    global message
    return n
def main() -> None:
    print(rejected(1))
main()
