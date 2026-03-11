#!/usr/bin/env python3

import math
import os
import argparse
import matplotlib.pyplot as plt

from shapely.geometry import box, Polygon
from shapely.ops import unary_union


def rounded_rect(padWidth, padHeight, r):

    base = box(-padWidth/2 + r, -padHeight/2, padWidth/2 - r, padHeight/2)
    side = box(-padWidth/2, -padHeight/2 + r, padWidth/2, padHeight/2 - r)

    corners = []

    for sx in [-1,1]:
        for sy in [-1,1]:

            cx = sx*(padWidth/2 - r)
            cy = sy*(padHeight/2 - r)

            circle = Polygon([
                (
                    cx + r*math.cos(t),
                    cy + r*math.sin(t)
                )
                for t in [i*math.pi/32 for i in range(65)]
            ])

            corners.append(circle)

    return unary_union([base, side] + corners)


def offset(shape, amount):
    return shape.buffer(-amount, join_style=1)


def create_fingers(padWidth, padHeight, trackWidth, trackSpacing):

    fingers = []

    y = -padHeight/2
    i = 0

    while y < padHeight/2:

        rect = box(
            -padWidth/2,
            y,
            padWidth/2,
            y + trackWidth
        )

        fingers.append((i, rect))

        y += trackWidth + trackSpacing
        i += 1

    return fingers

def polygon_to_pad(poly, pad_number="1"):

    if poly.geom_type == "MultiPolygon":
        polys = poly.geoms
    else:
        polys = [poly]

    s = f"""
  (pad "{pad_number}" smd custom
    (at 0 0)
    (size 0 0)
    (layers F.Cu F.Mask)
    (options (clearance outline) (anchor rect))
    (primitives
"""

    for p in polys:

        pts = list(p.exterior.coords)

        s += "      (gr_poly\n        (pts\n"

        for x, y in pts:
            s += f"          (xy {x:.4f} {y:.4f})\n"

        s += """        )
        (width 0)
        (fill yes)
      )
"""

    s += """    )
  )
"""

    return s

def polygon_to_kicad(poly):

    if poly.is_empty:
        return ""

    if poly.geom_type == "MultiPolygon":
        return "\n".join(polygon_to_kicad(p) for p in poly.geoms)

    pts = list(poly.exterior.coords)

    s = "  (fp_poly (pts\n"

    for x,y in pts:
        s += f"    (xy {x:.4f} {y:.4f})\n"

    s += "  ) (layer F.Cu) (width 0) (fill solid))\n"

    return s


def plot_shapes(stages):

    n = len(stages)

    cols = 8
    rows = (n + cols - 1) // cols

    fig, axes = plt.subplots(rows, cols, figsize=(2*cols, 2*rows))
    axes = axes.flatten()

    for ax in axes[n:]:
        ax.axis("off")

    for i, (name, geom) in enumerate(stages):

        ax = axes[i]
        ax.set_title(name)

        def draw(g):

            if g.is_empty:
                return

            if g.geom_type == "Polygon":
                x, y = g.exterior.xy
                ax.fill(x, y, alpha=0.6)

            elif g.geom_type == "MultiPolygon":
                for p in g.geoms:
                    x, y = p.exterior.xy
                    ax.fill(x, y, alpha=0.6)

        draw(geom)

        ax.set_aspect("equal")
        ax.grid(True)

    plt.tight_layout()
    plt.show()


def generate(
        padWidth,
        padHeight,
        cornerRadius,
        trackWidth,
        trackSpacing,
        connectingTrackWidth,
        fingerEndSpacing,
        minCTspacing,
        filletRadius=0
):

    stages = []

    # ---- base shapes ----

    shape1 = rounded_rect(padWidth, padHeight, cornerRadius)
    stages.append(("shape1 rounded_rect", shape1))

    shape2 = offset(shape1, connectingTrackWidth)
    stages.append(("shape2 offset", shape2))

    shape3 = offset(shape2, fingerEndSpacing)
    stages.append(("shape3 offset", shape3))

    shape3_height = shape3.bounds[3] - shape3.bounds[1]

    # ---- divider ----

    divider = box(
        -minCTspacing/2,
        -padHeight/2,
        minCTspacing/2,
        padHeight/2
    )
    stages.append(("divider", divider))

    # ---- fingers ----

    fingers = create_fingers(padWidth, shape3_height, trackWidth, trackSpacing)

    # ---- crop shapes to shape 3 ----

    top = shape3.bounds[3]
    bottom = shape3.bounds[1]

    crop = box(-padWidth, bottom, padWidth, top)

    shape1 = shape1.intersection(crop)
    shape2 = shape2.intersection(crop)
    shape3 = shape3.intersection(crop)

    stages.append(("shape1 cropped", shape1))
    stages.append(("shape2 cropped", shape2))
    stages.append(("shape3 cropped", shape3))

    # ---- connecting tracks ----

    shape4 = shape1.difference(shape2)
    shape4 = shape4.difference(divider)

    stages.append(("shape4 connecting tracks", shape4))

    # ---- median masks ----

    left_half = box(-padWidth, -padHeight, 0, padHeight)
    right_half = box(0, -padHeight, padWidth, padHeight)

    shape5 = unary_union([
        shape3.intersection(left_half),
        shape2.intersection(right_half)
    ])

    shape6 = unary_union([
        shape3.intersection(right_half),
        shape2.intersection(left_half)
    ])

    stages.append(("shape5 mask", shape5))
    stages.append(("shape6 mask", shape6))

    # ---- split fingers ----

    left_fingers = []
    right_fingers = []

    for i, rect in fingers:

        if i % 2 == 0:
            left_fingers.append(rect)
        else:
            right_fingers.append(rect)

    # ---- crop fingers ----

    left_fingers = [f.intersection(shape6) for f in left_fingers]
    right_fingers = [f.intersection(shape5) for f in right_fingers]

    for i,f in enumerate(left_fingers):
        stages.append((f"left finger {i}", f))

    for i,f in enumerate(right_fingers):
        stages.append((f"right finger {i}", f))

    # ---- final union ----

    final = unary_union(
        left_fingers +
        right_fingers +
        [shape4]
    )

    stages.append(("final (pre-fillet)", final))

    if filletRadius > 0:
        # A tiny positive buffer merges coincident/touching edges (zero-width
        # contacts between fingers and connecting tracks) into solid geometry
        # before the erosion step, preventing them from severing into separate
        # pieces that would re-expose internal vertices on dilation.
        eps = filletRadius * 0.01
        final = final.buffer(eps, join_style=1)
        final = final.buffer(-(filletRadius + eps), join_style=1).buffer(filletRadius, join_style=1)
        stages.append(("final (filleted)", final))

    return final, stages


def write_kicad(modulename, poly):

    os.makedirs("idc.pretty", exist_ok=True)

    filename = f"idc.pretty/{modulename}.kicad_mod"

    with open(filename, "w") as f:

        f.write(f"(footprint {modulename}\n")
        f.write(" (layer F.Cu)\n")

        f.write(polygon_to_pad(poly))

        f.write(")\n")

    print("saved:", filename)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--modulename", required=True)
    parser.add_argument("--padWidth", type=float, required=True)
    parser.add_argument("--padHeight", type=float, required=True)
    parser.add_argument("--cornerRadius", type=float, required=True)

    parser.add_argument("--trackWidth", type=float, required=True)
    parser.add_argument("--trackSpacing", type=float, required=True)

    parser.add_argument("--connectingTrackWidth", type=float, required=True)
    parser.add_argument("--fingerEndSpacing", type=float, required=True)

    parser.add_argument("--minCTspacing", type=float, required=True)
    parser.add_argument("--filletRadius", type=float, default=0)

    args = parser.parse_args()

    poly, stages = generate(
        args.padWidth,
        args.padHeight,
        args.cornerRadius,
        args.trackWidth,
        args.trackSpacing,
        args.connectingTrackWidth,
        args.fingerEndSpacing,
        args.minCTspacing,
        args.filletRadius
    )

    plot_shapes(stages)

    write_kicad(args.modulename, poly)


if __name__ == "__main__":
    main()