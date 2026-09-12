from tpy.extern import native_global
from tpy import int32

score: int32 = native_global("engine::score")

def main() -> None:
    # Local should shadow the native global
    score: int32 = int32(42)
    print(score)

main()
