# unsafe_alloc with no type context -- T cannot be inferred
from tpy.unsafe import unsafe_alloc

def main() -> None:
    p = unsafe_alloc()  # tpyc: error(/No matching overload/)

main()
