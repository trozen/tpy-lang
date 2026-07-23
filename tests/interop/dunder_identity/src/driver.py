# Shared across ext-exec and cpy-parity: every assertion is aliasing plain
# Python exhibits identically (identity, write-through, un-sliced derived
# instances), so the whole case runs in parity -- no ext_checks needed.
import dunder_identity as m

g = m.Grid()
a = g[0]
print(a is g[1])
a.v = 42
print(g[0].v)
print(g[0] is g.cell())

s = m.Echo()
print(s[0] is s)
s[0].n = 5
print(s.n)

d = m.EchoSub()
print(d[0] is d, type(d[0]) is m.EchoSub)

e = m.KeyEcho()
k = m.Node(1)
print(e[k] is k)
e[k].v = 7
print(k.v)

x = m.Acc(3)
y = m.Acc(1)
print((x + y) is x)
(x + y).n = 20
print(x.n)
y.n = 50
print((x + y) is y)
print((-y) is y)

r = m.RoPick(2)
q = m.RoPick(9)
print((r + q) is r)
