from tpy.extern import native_global
from tpy import Int32

score: Int32 = native_global("engine::score")

def main() -> None:
    # Local should shadow the native global
    score: Int32 = Int32(42)
    print(score)

main()
