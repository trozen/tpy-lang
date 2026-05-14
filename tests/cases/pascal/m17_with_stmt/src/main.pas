{ M17: `with rec do <stmt>` -- bare references to `rec`'s fields
  inside the body are rewritten by the translator to qualified
  field accesses. The receiver must be a record variable; richer
  receiver shapes (function-call result, pointer deref) are
  deferred. }
program WithStmt;
type
  Point = record
    x, y: integer;
    name: string;
  end;
var
  p: Point;
begin
  p.x := 0;
  p.y := 0;
  with p do
  begin
    x := 3;
    y := 4;
    name := 'origin-offset';
  end;
  writeln(p.x, ',', p.y);
  writeln(p.name);
end.
