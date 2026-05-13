{ M4: recursion with multiple recursive call sites. }
program Fib;
function fib(n: integer): integer;
begin
  if n < 2 then
    fib := n
  else
    fib := fib(n - 1) + fib(n - 2);
end;
var
  i: integer;
begin
  for i := 0 to 10 do
    writeln(fib(i));
end.
