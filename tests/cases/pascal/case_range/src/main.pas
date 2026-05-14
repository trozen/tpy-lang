{ M12: `case` arms with range labels. Mixes single-value labels,
  comma-separated lists, and range labels (`1..3`) in one statement.
  Enum-typed range labels (`Mon..Fri`) are expanded to one
  MatchValue arm per enum member in the closed interval at
  translate time -- sidesteps the missing ordering comparisons on
  enum types. }
program CaseRange;
type
  Day = (Mon, Tue, Wed, Thu, Fri, Sat, Sun);
var
  n: integer;
  i: integer;
  d: Day;

procedure classify(d: Day);
begin
  case d of
    Mon..Fri: writeln('weekday');
    Sat..Sun: writeln('weekend');
  end;
end;

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
  d := Wed;
  classify(d);
  d := Sat;
  classify(d);
end.
