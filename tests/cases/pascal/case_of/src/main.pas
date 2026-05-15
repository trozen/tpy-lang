{ M3: case/of with literal value arms (single + multi-value),
  else branch, inside a for loop. }
program CaseTest;
var
  i: integer;
begin
  for i := 1 to 5 do
  begin
    case i of
      1: writeln('one');
      2, 3: writeln('two-or-three');
      4: writeln('four');
    else
      writeln('other');
    end;
  end;
end.
