# Negative literal index beyond container bounds should panic
from tpy import Int32

def main() -> None:
    nums: list[Int32] = [10, 20, 30]
    print(nums[-4])

main()
