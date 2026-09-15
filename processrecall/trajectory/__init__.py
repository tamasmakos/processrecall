"""trajectory — the harness seam: raw agent activity arriving as events.

Bottom-up decode. A :class:`TrajectorySource` hands the rest of the system one
canonical, OTel-aligned event per completed tool action, whatever harness
produced it; a :class:`GuidanceSink` carries the return trip. Nothing below this
layer knows what a hook payload or a transcript line looks like.

On the hot path, so stdlib only.
"""
