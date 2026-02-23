# Warning: ALL_CAPS module-level variable without Final annotation
from tpy import Int32

MAX_SIZE: Int32 = 100  # tpyc: warning(/without Final/)

def main() -> None:
    print(MAX_SIZE)

main()
