from tpy import int32

class Point:
    x: int32
    y: int32

# Global variable - lives for the duration of the program
ORIGIN: Point = Point()

def get_origin() -> Point:
    return ORIGIN  # tpyc: ok (global lives forever)

def main():
    ORIGIN.x = 100
    ORIGIN.y = 200

    ref: Point = get_origin()
    print(ref.x)
    print(ref.y)

main()
