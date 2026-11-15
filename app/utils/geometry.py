"""Pure geometry helpers (no Qt) so multi-monitor logic is unit-testable."""
from __future__ import annotations

Rect = tuple[int, int, int, int]  # x, y, w, h

MIN_VISIBLE_W = 80   # px of the title bar that must stay reachable
MIN_VISIBLE_H = 24


def _intersection(a: Rect, b: Rect) -> int:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(w, 0) * max(h, 0)


def clamp_to_screens(rect: Rect, screens: list[Rect], min_size: tuple[int, int] = (240, 120)) -> Rect:
    """Return `rect` adjusted so the window is usable on the given screens.

    - shrinks windows larger than the best screen
    - if the title-bar strip is not reachable on any screen (monitor unplugged,
      resolution lowered) moves the window onto the screen it overlaps most
      (first screen if none).
    """
    if not screens:
        return rect
    x, y, w, h = rect
    bar = (x, y, w, MIN_VISIBLE_H)
    reachable = any(_intersection(bar, s) >= MIN_VISIBLE_W * MIN_VISIBLE_H for s in screens)
    target = max(screens, key=lambda s: _intersection(rect, s))
    if _intersection(rect, target) == 0:
        target = screens[0]
    w = max(min_size[0], min(w, target[2]))
    h = max(min_size[1], min(h, target[3]))
    if not reachable:
        x = min(max(x, target[0]), target[0] + target[2] - w)
        y = min(max(y, target[1]), target[1] + target[3] - h)
    else:
        # fully reachable already; only keep the window from hanging far off-screen
        x = min(max(x, target[0] - w + MIN_VISIBLE_W), target[0] + target[2] - MIN_VISIBLE_W)
        y = min(max(y, target[1]), target[1] + target[3] - MIN_VISIBLE_H)
    return (x, y, w, h)
