"""guidance — top-down encode: a position in the graph becomes advice.

The read direction of the same traversal ``graph`` writes. Locate the agent from
its previous step, extract the neighbourhood, decide whether any trigger fires,
and only then render. Silence is the default, and it is counted.

On the hot path, so stdlib only.
"""
