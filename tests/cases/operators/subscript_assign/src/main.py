from tpy import Int32, Array

# Array subscript assignment
arr: Array[Int32, 3] = [1, 2, 3]
arr[0] = 100
arr[1] = 200
arr[2] = 300
print(arr[0])
print(arr[1])
print(arr[2])

# list subscript assignment
items: list[Int32] = [10, 20]
items[0] = 99
items[1] = 88
print(items[0])
print(items[1])
