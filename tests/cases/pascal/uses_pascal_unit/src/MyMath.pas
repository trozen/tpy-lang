unit MyMath;
interface
function square(n: integer): integer;
function cube(n: integer): integer;
implementation
function square(n: integer): integer;
begin
  square := n * n;
end;
function cube(n: integer): integer;
begin
  cube := n * n * n;
end;
end.
