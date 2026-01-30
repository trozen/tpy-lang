from tpy import Int32

# Test that subscript out-of-bounds access on list panics at runtime
# (uses tpy::get_value() internally)

def test_list_subscript_oob() -> None:
    nums: list[Int32] = [1, 2, 3]

    # Access index 10 via subscript - out of bounds (only 3 elements)
    x: Int32 = nums[10]
    print(x)

test_list_subscript_oob()
