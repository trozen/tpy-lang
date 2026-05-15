{ M18: typed-record-consts. `const p: Point = (x: 1; y: 2);`
  lowers to a default-constructed record + per-field assignment
  (TPy doesn't auto-derive a keyword-arg constructor when every
  field has a default). The user-facing behaviour matches TP7 --
  the "const" is value-initialised once at module load and isn't
  enforced as immutable past that point. }
program RecordConsts;
type
  Point = record
    x, y: integer;
  end;
  Color = record
    r, g, b: integer;
  end;
const
  Origin: Point = (x: 0; y: 0);
  Hot: Color = (r: 255; g: 64; b: 32);
begin
  writeln('origin=', Origin.x, ',', Origin.y);
  writeln('hot=', Hot.r, ',', Hot.g, ',', Hot.b);
end.
