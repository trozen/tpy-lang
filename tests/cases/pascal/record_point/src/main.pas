{ M5: type/record declaration, field access on assign + read, writeln
  dispatch on field access. }
program PointTest;
type
  Point = record
    x, y: integer;
  end;
var
  p: Point;
begin
  p.x := 10;
  p.y := 20;
  writeln(p.x);
  writeln(p.y);
  writeln(p.x + p.y);
end.
