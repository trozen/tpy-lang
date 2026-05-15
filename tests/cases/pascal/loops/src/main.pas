{ M3: while + repeat/until exercise.
  Sums 1..5 two ways; both should print 15. }
program Loops;
var
  i, total, ascending, descending: integer;
begin
  total := 0;
  i := 1;
  while i <= 5 do
  begin
    total := total + i;
    i := i + 1;
  end;
  ascending := total;

  total := 0;
  i := 5;
  repeat
    total := total + i;
    i := i - 1;
  until i = 0;
  descending := total;

  writeln(ascending);
  writeln(descending);
end.
