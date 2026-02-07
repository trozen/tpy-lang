def main():
    # Whole numbers should show .0
    print(1.0)
    print(5.0)
    print(100.0)
    print(-42.0)

    # Simple decimals
    print(0.5)
    print(3.14)
    print(-2.5)

    # The classic 0.1 representation
    print(0.1)
    print(0.2)
    print(0.3)

    # High precision values
    print(3.14159265358979)
    print(2.718281828459045)

    # Large numbers (should not use scientific notation)
    print(123456789.0)
    print(123456789.123456)
    print(9999999999.999999)

    # Small decimals
    print(0.000123)
    print(0.0001)
    print(0.00001)

    # Negative small decimals
    print(-0.000123)
    print(-0.00001)

    # Trailing zeros should be trimmed (but keep at least .X)
    x = 1.10
    print(x)
    y = 2.500
    print(y)

    # Very small numbers
    print(0.000000001)
    print(0.0000000001)

    # Numbers close to integers
    print(1.0000000001)
    print(0.9999999999)

main()
