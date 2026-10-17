"""Firma como trazos: validacion de que la dibujo una persona, rasterizado y hash perceptual.

Formato de un punto: [x, y, t, p]
  x, y: coordenadas normalizadas 0..1 sobre el lienzo 756x280
  t:    milisegundos desde el primer punto de la firma
  p:    presion 0..1 (opcional, 0.5 si el dispositivo no la reporta)
"""

import io
import math
from dataclasses import asdict, dataclass
from typing import Annotated

from PIL import Image, ImageDraw
from pydantic import Field

WIDTH, HEIGHT = 756, 280
SUPERSAMPLE = 2

MIN_POINTS, MAX_POINTS = 20, 4000
MAX_STROKES = 40
MIN_DURATION_MS, MAX_DURATION_MS = 400, 5 * 60 * 1000
MIN_BBOX_W, MIN_BBOX_H = 0.15, 0.10

Point = Annotated[list[float], Field(min_length=3, max_length=4)]
Stroke = Annotated[list[Point], Field(min_length=1, max_length=MAX_POINTS)]
Strokes = Annotated[list[Stroke], Field(min_length=1, max_length=MAX_STROKES)]


class InvalidSignature(ValueError):
    """La firma no parece hecha por una persona. El mensaje es apto para mostrar al usuario."""


@dataclass
class StrokeStats:
    points: int
    strokes: int
    duration_ms: int
    bbox_w: float
    bbox_h: float

    def as_dict(self) -> dict:
        return asdict(self)


def _flat(strokes: list[list[list[float]]]) -> list[list[float]]:
    return [pt for stroke in strokes for pt in stroke]


def validate_human(strokes: list[list[list[float]]]) -> StrokeStats:
    pts = _flat(strokes)
    n = len(pts)
    if n < MIN_POINTS:
        raise InvalidSignature("La firma es demasiado corta. Dibuja tu firma completa.")
    if n > MAX_POINTS:
        raise InvalidSignature("La firma tiene demasiados puntos.")

    prev_t = -1.0
    for x, y, t, *rest in pts:
        p = rest[0] if rest else 0.5
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 <= p <= 1) or not all(map(math.isfinite, (x, y, t, p))):
            raise InvalidSignature("La firma tiene puntos fuera del lienzo.")
        if t < prev_t:
            raise InvalidSignature("La firma llegó desordenada. Vuelve a intentarlo.")
        prev_t = t

    duration = pts[-1][2] - pts[0][2]
    if duration < MIN_DURATION_MS:
        raise InvalidSignature("La firma se dibujó demasiado rápido.")
    if duration > MAX_DURATION_MS:
        raise InvalidSignature("La firma tardó demasiado. Vuelve a dibujarla.")

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    bbox_w, bbox_h = max(xs) - min(xs), max(ys) - min(ys)
    if bbox_w < MIN_BBOX_W or bbox_h < MIN_BBOX_H:
        raise InvalidSignature("La firma es muy pequeña. Usa más espacio del recuadro.")

    if _is_straight_line(xs, ys):
        raise InvalidSignature("Una línea recta no es una firma.")

    if _is_robotic(strokes):
        raise InvalidSignature("No pudimos validar la firma. Dibújala a mano.")

    return StrokeStats(points=n, strokes=len(strokes), duration_ms=int(duration),
                       bbox_w=round(bbox_w, 3), bbox_h=round(bbox_h, 3))


def _is_straight_line(xs: list[float], ys: list[float]) -> bool:
    """Componentes principales: si el eje menor es casi nulo, todos los puntos estan en una recta."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs) / n
    syy = sum((y - my) ** 2 for y in ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    disc = math.sqrt(max(tr * tr / 4 - det, 0))
    big, small = tr / 2 + disc, max(tr / 2 - disc, 0)
    return big == 0 or math.sqrt(small / big) < 0.03


def _cv(values: list[float]) -> float:
    if len(values) < 2:
        return 1.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(var) / mean


def _is_robotic(strokes: list[list[list[float]]]) -> bool:
    """Un script suele mover el puntero a velocidad constante con intervalos exactos.
    Una persona acelera, frena y el navegador entrega eventos con jitter."""
    intervals, speeds = [], []
    for stroke in strokes:
        for a, b in zip(stroke, stroke[1:]):
            dt = b[2] - a[2]
            intervals.append(dt)
            if dt > 0:
                speeds.append(math.hypot((b[0] - a[0]) * WIDTH, (b[1] - a[1]) * HEIGHT) / dt)
    if len(intervals) < 10:
        return False
    if all(dt == 0 for dt in intervals):
        return True
    return len(speeds) >= 10 and _cv(speeds) < 0.08 and _cv(intervals) < 0.01


def rasterize(strokes: list[list[list[float]]]) -> Image.Image:
    """Dibuja la firma en negro sobre blanco, 756x280, con grosor segun la presion."""
    w, h = WIDTH * SUPERSAMPLE, HEIGHT * SUPERSAMPLE
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)
    for stroke in strokes:
        pts = [(p[0] * w, p[1] * h, p[3] if len(p) > 3 else 0.5) for p in stroke]
        for (x0, y0, p0), (x1, y1, p1) in zip(pts, pts[1:]):
            width = _ink_width((p0 + p1) / 2)
            draw.line((x0, y0, x1, y1), fill=20, width=round(width))
        for x, y, p in pts:
            r = _ink_width(p) / 2
            draw.ellipse((x - r, y - r, x + r, y + r), fill=20)
    return img.resize((WIDTH, HEIGHT), Image.LANCZOS)


def _ink_width(pressure: float) -> float:
    return 3.2 * SUPERSAMPLE * (0.6 + 0.8 * pressure)


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def dhash(img: Image.Image) -> str:
    """Hash perceptual de 64 bits: resiste cambios de pocos pixeles, a diferencia de un SHA."""
    small = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = small.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = px[row * 9 + col], px[row * 9 + col + 1]
            bits = (bits << 1) | (left > right)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")
