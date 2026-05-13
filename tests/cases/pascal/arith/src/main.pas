{ M2: integer var decls, arithmetic with operator precedence,
  assignment, and writeln of an Int32 expression.
  Expected output: 49 = 10 + 20*2 - 1. }
program Arith;
var
  a, b, c: integer;
begin
  a := 10;
  b := 20;
  c := a + b * 2 - 1;
  writeln(c);
end.
