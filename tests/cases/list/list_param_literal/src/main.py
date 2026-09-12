from tpy import int32, Array

def takes_list(x: list[int]) -> None:
    x.append(42)
    print(len(x))

def takes_list_int32(x: list[int32]) -> None:
    x.append(int32(99))
    print(len(x))

def takes_array(x: Array[int32, 3]) -> None:
    print(x[0])

# Empty list literals
takes_list([])
takes_list_int32([])

# Empty list constructors
takes_list(list())
takes_list_int32(list())

# Non-empty list literals
takes_list([1, 2, 3])

# Array literals
takes_array([int32(10), int32(20), int32(30)])
