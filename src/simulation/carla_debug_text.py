"""Drawing readable text in a CARLA world, at a size you choose.

``DebugHelper.draw_string`` takes no font size. It renders at a fixed pixel
size whatever the distance, which on a 4K monitor is a few pixels tall and
unreadable — and there is no parameter, anywhere, that changes it.

So text has to be drawn as geometry. This is a stroke font: every glyph is a
handful of polylines on a 3x5 grid, drawn with ``draw_line``, which means it
scales to any height and can be turned to face the camera.

**It costs a call per stroke.** A ten-character word is roughly forty
``draw_line`` calls, so callers are expected to draw few words, rarely, and
to keep them short. That is the price of legible text and there is no
cheaper way to buy it.

Lowercase is folded to uppercase: it halves the table for no loss, since
everything drawn here is an identifier or a number.
"""

import carla

# Glyphs on a 3-wide, 5-tall grid, y upward from the baseline. Each entry is
# a list of polylines, and each polyline is drawn as connected segments.
# Deliberately blocky: curves would cost strokes and buy nothing at the size
# these are read at.
_W, _H = 2.0, 4.0

GLYPHS: dict[str, list[list[tuple[float, float]]]] = {
    " ": [],
    "0": [[(0, 0), (2, 0), (2, 4), (0, 4), (0, 0)]],
    "1": [[(0, 3), (1, 4), (1, 0)]],
    "2": [[(0, 4), (2, 4), (2, 2), (0, 2), (0, 0), (2, 0)]],
    "3": [[(0, 4), (2, 4), (2, 0), (0, 0)], [(0, 2), (2, 2)]],
    "4": [[(0, 4), (0, 2), (2, 2)], [(2, 4), (2, 0)]],
    "5": [[(2, 4), (0, 4), (0, 2), (2, 2), (2, 0), (0, 0)]],
    "6": [[(2, 4), (0, 4), (0, 0), (2, 0), (2, 2), (0, 2)]],
    "7": [[(0, 4), (2, 4), (2, 0)]],
    "8": [[(0, 0), (2, 0), (2, 4), (0, 4), (0, 0)], [(0, 2), (2, 2)]],
    "9": [[(2, 2), (0, 2), (0, 4), (2, 4), (2, 0), (0, 0)]],
    "A": [[(0, 0), (0, 3), (1, 4), (2, 3), (2, 0)], [(0, 2), (2, 2)]],
    "B": [[(0, 0), (0, 4), (2, 4), (2, 2), (0, 2)], [(2, 2), (2, 0), (0, 0)]],
    "C": [[(2, 4), (0, 4), (0, 0), (2, 0)]],
    "D": [[(0, 0), (0, 4), (1.5, 4), (2, 3), (2, 1), (1.5, 0), (0, 0)]],
    "E": [[(2, 4), (0, 4), (0, 0), (2, 0)], [(0, 2), (1.5, 2)]],
    "F": [[(2, 4), (0, 4), (0, 0)], [(0, 2), (1.5, 2)]],
    "G": [[(2, 4), (0, 4), (0, 0), (2, 0), (2, 2), (1, 2)]],
    "H": [[(0, 4), (0, 0)], [(2, 4), (2, 0)], [(0, 2), (2, 2)]],
    "I": [[(1, 4), (1, 0)], [(0, 4), (2, 4)], [(0, 0), (2, 0)]],
    "J": [[(2, 4), (2, 1), (1, 0), (0, 1)]],
    "K": [[(0, 4), (0, 0)], [(2, 4), (0, 2), (2, 0)]],
    "L": [[(0, 4), (0, 0), (2, 0)]],
    "M": [[(0, 0), (0, 4), (1, 2.5), (2, 4), (2, 0)]],
    "N": [[(0, 0), (0, 4), (2, 0), (2, 4)]],
    "O": [[(0, 0), (0, 4), (2, 4), (2, 0), (0, 0)]],
    "P": [[(0, 0), (0, 4), (2, 4), (2, 2), (0, 2)]],
    "Q": [[(0, 0), (0, 4), (2, 4), (2, 1), (1, 0), (0, 0)], [(1, 1), (2, 0)]],
    "R": [[(0, 0), (0, 4), (2, 4), (2, 2), (0, 2)], [(1, 2), (2, 0)]],
    "S": [[(2, 4), (0, 4), (0, 2), (2, 2), (2, 0), (0, 0)]],
    "T": [[(0, 4), (2, 4)], [(1, 4), (1, 0)]],
    "U": [[(0, 4), (0, 0), (2, 0), (2, 4)]],
    "V": [[(0, 4), (1, 0), (2, 4)]],
    "W": [[(0, 4), (0.5, 0), (1, 2), (1.5, 0), (2, 4)]],
    "X": [[(0, 4), (2, 0)], [(0, 0), (2, 4)]],
    "Y": [[(0, 4), (1, 2), (2, 4)], [(1, 2), (1, 0)]],
    "Z": [[(0, 4), (2, 4), (0, 0), (2, 0)]],
    # Below the baseline, so it cannot be read as part of the letter beside
    # it -- stop ids are mostly underscores holding two halves apart.
    "_": [[(0, -0.6), (2, -0.6)]],
    "-": [[(0, 2), (2, 2)]],
    "+": [[(0, 2), (2, 2)], [(1, 1), (1, 3)]],
    ">": [[(0, 3.5), (2, 2), (0, 0.5)]],
    "<": [[(2, 3.5), (0, 2), (2, 0.5)]],
    "/": [[(0, 0), (2, 4)]],
    ":": [[(1, 2.6), (1, 3.0)], [(1, 1.0), (1, 1.4)]],
    ".": [[(1, 0), (1, 0.4)]],
    "%": [[(0, 0), (2, 4)], [(0, 3.4), (0, 4)], [(2, 0), (2, 0.6)]],
    "(": [[(1.5, 4), (0.5, 3), (0.5, 1), (1.5, 0)]],
    ")": [[(0.5, 4), (1.5, 3), (1.5, 1), (0.5, 0)]],
    "?": [[(0, 3), (1, 4), (2, 3), (1, 2), (1, 1.4)], [(1, 0), (1, 0.4)]],
    "!": [[(1, 4), (1, 1.4)], [(1, 0), (1, 0.4)]],
    "=": [[(0, 1.4), (2, 1.4)], [(0, 2.6), (2, 2.6)]],
}

# Gap between glyph boxes, as a fraction of glyph width.
TRACKING = 0.45
# Stroke width, as a fraction of cap height. Thin enough not to bloom the
# counters into each other; the debug material is emissive.
STROKE_FRACTION = 0.09
# Anything not in the table is drawn as this, so a surprise is visible
# rather than silently missing.
FALLBACK = "?"


def advance(height: float) -> float:
    """
    Return how far one character moves the cursor.

    :param height: Cap height of the text, in metres.
    :return: Horizontal step per character, in metres.
    """
    return height * (_W + TRACKING) / _H


def text_width(text: str, height: float) -> float:
    """
    Return how wide a string will be drawn.

    :param text: The string that would be drawn.
    :param height: Cap height, in metres.
    :return: Width in metres, including the trailing gap.
    """
    return advance(height) * len(text)


def draw_text(
    debug: object,
    text: str,
    origin: "carla.Location",
    right: "carla.Vector3D",
    up: "carla.Vector3D",
    height: float,
    color: "carla.Color",
    life_time: float,
    thickness: float | None = None,
) -> int:
    """
    Draw a string into the world as line strokes.

    The caller supplies the plane: ``right`` and ``up`` are unit vectors, so
    text can be laid flat on the ground, stood upright, or turned to face
    the camera by passing the spectator's own axes.
    :param debug: The world's debug helper.
    :param text: What to draw. Folded to uppercase; unknown characters are
        drawn as ``FALLBACK``.
    :param origin: Baseline start of the first character.
    :param right: Unit vector along the text direction.
    :param up: Unit vector for the character's vertical.
    :param height: Cap height in metres.
    :param color: Stroke colour.
    :param life_time: Seconds the strokes live, 0 for permanent.
    :param thickness: Stroke width in metres. Defaults to a fraction of the
        height, which keeps the weight right at any size.
    :return: How many line segments were drawn, for cost accounting.
    """
    if thickness is None:
        thickness = height * STROKE_FRACTION
    scale = height / _H
    step = advance(height)
    drawn = 0

    for index, character in enumerate(text.upper()):
        strokes = GLYPHS.get(character, GLYPHS[FALLBACK])
        left = step * index
        for polyline in strokes:
            points = [
                carla.Location(
                    x=origin.x + right.x * (left + gx * scale) + up.x * gy
                    * scale,
                    y=origin.y + right.y * (left + gx * scale) + up.y * gy
                    * scale,
                    z=origin.z + right.z * (left + gx * scale) + up.z * gy
                    * scale,
                )
                for gx, gy in polyline
            ]
            for start, end in zip(points, points[1:]):
                debug.draw_line(start, end, thickness=thickness,
                                color=color, life_time=life_time)
                drawn += 1
    return drawn


def stroke_count(text: str) -> int:
    """
    Return how many segments a string costs, without drawing it.

    Useful for budgeting: this module is called on a tick path, and the cost
    is per stroke rather than per string.
    :param text: The string that would be drawn.
    :return: Number of ``draw_line`` calls it would make.
    """
    total = 0
    for character in text.upper():
        for polyline in GLYPHS.get(character, GLYPHS[FALLBACK]):
            total += max(0, len(polyline) - 1)
    return total
