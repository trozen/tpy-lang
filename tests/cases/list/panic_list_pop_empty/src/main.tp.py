from tpy import Int32

def main() -> None:
    items: list[Int32] = [1]
    items.pop()  # Remove the only element
    x: Int32 = items.pop()  # Should panic: pop from empty list
    print(x)

main()
