import math

def main():
    # Test math.log (natural log)
    x = math.log(2.718281828)
    if x > 0.99 and x < 1.01:
        print("log(e) ok")
    else:
        print("log(e) error")

    # Test math.log with base
    y = math.log(8.0, 2.0)
    if y > 2.99 and y < 3.01:
        print("log(8,2) ok")
    else:
        print("log(8,2) error")

    # Test math.sqrt
    z = math.sqrt(4.0)
    if z > 1.99 and z < 2.01:
        print("sqrt ok")
    else:
        print("sqrt error")

    # Test math.sin/cos
    s = math.sin(0.0)
    c = math.cos(0.0)
    if s > -0.01 and s < 0.01 and c > 0.99 and c < 1.01:
        print("sin/cos ok")
    else:
        print("sin/cos error")

    # Test math.floor/ceil
    f = math.floor(3.7)
    ce = math.ceil(3.2)
    if f > 2.99 and f < 3.01 and ce > 3.99 and ce < 4.01:
        print("floor/ceil ok")
    else:
        print("floor/ceil error")

main()
