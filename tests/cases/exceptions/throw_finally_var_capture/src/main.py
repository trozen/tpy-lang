# finally body references variables declared in the try body
from tpy import int32, Own

def get_int() -> int32:
    return 42

def get_str() -> str:
    return "hello"

def get_list() -> Own[list[int32]]:
    return [1, 2, 3]

def main() -> None:
    # int32 -- trivial type
    try:
        x = get_int()
    finally:
        print(x)

    # str (std::string)
    try:
        s = get_str()
    finally:
        print(s)

    # list (std::vector, non-value type)
    try:
        items = get_list()
    finally:
        print(len(items))

main()
