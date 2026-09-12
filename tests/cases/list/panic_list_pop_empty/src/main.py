from tpy import int32

def main() -> None:
    items: list[int32] = [1]
    items.pop()  # Remove the only element
    x: int32 = items.pop()  # Should panic: pop from empty list
    print(x)

main()
