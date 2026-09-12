# Negative literal index beyond container bounds should panic
from tpy import int32

def main() -> None:
    nums: list[int32] = [10, 20, 30]
    print(nums[-4])

main()
