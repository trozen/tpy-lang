{ M12: `case` arms with range labels. Mixes single-value labels,
  comma-separated lists, and range labels (`1..3`) in one statement.
  Enum-typed range labels (e.g. `Mon..Fri`) are intentionally not
  exercised here -- they need ordering comparisons on enums, which
  TPy doesn't expose yet; the workaround is to enumerate the values
  via the comma-separated label form. }
program CaseRange;
var
  n: integer;
  i: integer;
begin
  for i := 0 to 8 do
  begin
    n := i;
    case n of
      0:        writeln('zero');
      1..3:     writeln('low');
      4, 5:     writeln('mid');
      6..9:     writeln('high');
      else      writeln('out');
    end;
  end;
end.
