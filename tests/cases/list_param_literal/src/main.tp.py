from tpy import Int32, Array, StaticList

def takes_list(x: list[int]) -> None:
    x.append(42)
    print(len(x))

def takes_list_int32(x: list[Int32]) -> None:
    x.append(Int32(99))
    print(len(x))

def takes_array(x: Array[Int32, 3]) -> None:
    print(x[0])

def takes_static(x: StaticList[Int32, 10]) -> None:
    x.append(Int32(1))
    print(len(x))

# Empty list literals
takes_list([])
takes_list_int32([])

# Empty list constructors
takes_list(list())
takes_list_int32(list())

# Non-empty list literals
takes_list([1, 2, 3])

# Array literals
takes_array([Int32(10), Int32(20), Int32(30)])

# StaticList constructors
takes_static(StaticList[Int32, 10]())
