import pytest

import strokes as s
from conftest import human_strokes


def test_human_signature_passes():
    stats = s.validate_human(human_strokes())
    assert stats.points == 120 and stats.strokes == 3


def test_too_few_points():
    with pytest.raises(s.InvalidSignature, match="corta"):
        s.validate_human([[[0.1, 0.1, 0, 0.5], [0.5, 0.5, 500, 0.5]]])


def test_dot_is_too_small():
    stroke = [[0.5 + i * 0.0005, 0.5, i * 30, 0.5] for i in range(30)]
    with pytest.raises(s.InvalidSignature, match="pequeña"):
        s.validate_human([stroke])


def test_straight_diagonal_line_rejected():
    stroke = [[0.1 + i * 0.02, 0.1 + i * 0.02, i * 17 + (i % 3), 0.5] for i in range(40)]
    with pytest.raises(s.InvalidSignature, match="recta"):
        s.validate_human([stroke])


def test_too_fast():
    pts = human_strokes()
    for stroke in pts:
        for p in stroke:
            p[2] = p[2] / 100
    with pytest.raises(s.InvalidSignature, match="rápido"):
        s.validate_human(pts)


def test_out_of_range_and_unordered():
    pts = human_strokes()
    pts[0][3][0] = 1.5
    with pytest.raises(s.InvalidSignature, match="fuera"):
        s.validate_human(pts)
    pts = human_strokes()
    pts[1][0][2] = 0
    with pytest.raises(s.InvalidSignature, match="desordenada"):
        s.validate_human(pts)


def test_robotic_constant_speed_rejected():
    import math
    stroke = []
    for i in range(60):
        a = i / 59 * math.pi * 2
        # Círculo en píxeles (el lienzo es 756x280) recorrido a velocidad y ritmo constantes
        stroke.append([0.5 + 0.15 * math.cos(a), 0.5 + 0.15 * 756 / 280 * math.sin(a), i * 16, 0.5])
    with pytest.raises(s.InvalidSignature, match="a mano"):
        s.validate_human([stroke])


def test_rasterize_is_fixed_size_and_hash_stable():
    img = s.rasterize(human_strokes())
    assert img.size == (756, 280)
    h1 = s.dhash(img)
    h2 = s.dhash(s.rasterize(human_strokes(seed=1)))
    assert s.hamming(h1, h2) == 0
    assert s.to_png(img).startswith(b"\x89PNG")


def test_near_duplicate_detected_but_different_signature_not():
    a = s.dhash(s.rasterize(human_strokes(seed=1)))
    b = s.dhash(s.rasterize(human_strokes(seed=1, shift=0.004)))
    c = s.dhash(s.rasterize([[[1 - p[0], 1 - p[1], p[2], p[3]] for p in st] for st in human_strokes(seed=5)]))
    assert s.hamming(a, b) <= 6
    assert s.hamming(a, c) > 6
