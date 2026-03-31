# Test quote/add_method_from_source macro APIs.
from tpy import Int32
from builder import builder

@builder
class Point:
    x: Int32
    y: Int32

@builder
class Person:
    name: str
    age: Int32

def test_basic() -> None:
    p = Point(1, 2)
    print(p.x)
    print(p.y)

def test_setter() -> None:
    p = Point(0, 0)
    p.set_x(10)
    p.set_y(20)
    print(p.x)
    print(p.y)

def test_eq() -> None:
    a = Point(1, 2)
    b = Point(1, 2)
    c = Point(3, 4)
    print(a == b)
    print(a == c)

def test_describe() -> None:
    p = Point(5, 10)
    print(p.describe())
    q = Person("Alice", 30)
    print(q.describe())

def test_reset() -> None:
    p = Point(5, 10)
    p.reset()
    print(p.x)
    print(p.y)

def test_static() -> None:
    print(Point.field_count())
    print(Person.field_count())
    p = Point(1, 2)
    print(p.field_count())

def test_quote_expr() -> None:
    p = Point(5, 10)
    print(p.default_first())
    q = Person("Alice", 30)
    print(q.default_first())

test_basic()
test_setter()
test_eq()
test_describe()
test_reset()
test_static()
test_quote_expr()
