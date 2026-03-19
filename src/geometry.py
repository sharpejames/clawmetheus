import math

def circle(cx, cy, radius, num_points=64):
    """Full circle as a list of {x, y} points."""
    points = []
    for i in range(num_points + 1):
        angle = 2 * math.pi * i / num_points
        points.append({"x": int(cx + radius * math.cos(angle)), "y": int(cy + radius * math.sin(angle))})
    return points

def arc(cx, cy, radius, start_rad, end_rad, n=32):
    """Arc segment from start_rad to end_rad (radians)."""
    points = []
    for i in range(n + 1):
        angle = start_rad + (end_rad - start_rad) * i / n
        points.append({"x": int(cx + radius * math.cos(angle)), "y": int(cy + radius * math.sin(angle))})
    return points

def line(x1, y1, x2, y2, num_points=10):
    """Straight line as interpolated points."""
    points = []
    for i in range(num_points + 1):
        t = i / num_points
        points.append({"x": int(x1 + (x2 - x1) * t), "y": int(y1 + (y2 - y1) * t)})
    return points

def bezier(p0, p1, p2, p3, n=32):
    """Cubic bezier curve. Each point is (x, y) tuple."""
    points = []
    for i in range(n + 1):
        t = i / n
        x = (1-t)**3*p0[0] + 3*(1-t)**2*t*p1[0] + 3*(1-t)*t**2*p2[0] + t**3*p3[0]
        y = (1-t)**3*p0[1] + 3*(1-t)**2*t*p1[1] + 3*(1-t)*t**2*p2[1] + t**3*p3[1]
        points.append({"x": int(x), "y": int(y)})
    return points
