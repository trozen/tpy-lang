from tpy import Int32

# Test that out-of-bounds access on list panics at runtime

def test_out_of_bounds() -> None:
    items: list[Int32] = [10, 20, 30]

    # Access index 10 which is out of bounds (only 3 elements)
    x: Int32 = items[10]
    print(x)

test_out_of_bounds()
