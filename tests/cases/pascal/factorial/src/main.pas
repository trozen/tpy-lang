{ M4: recursive function, value parameter, return-by-name-assign. }
program FactorialTest;
function factorial(n: integer): integer;
begin
  if n <= 1 then
    factorial := 1
  else
    factorial := n * factorial(n - 1);
end;
var
  i: integer;
begin
  for i := 1 to 6 do
    writeln(factorial(i));
end.
