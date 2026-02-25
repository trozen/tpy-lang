# Test returning a view local from a str-returning function
def make_greeting(name: str) -> str:
    result = "hello " + name
    return result

def echo(msg: str) -> str:
    s = msg
    return s

print(make_greeting("world"))
print(echo("test"))
