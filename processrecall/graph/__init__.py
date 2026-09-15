"""graph — the two layers and the single traversal they share.

The episodic index records one row per observed step; the abstract graph lifts
those rows into procedures and the permissible transitions between them. Both
are the same memory read two ways, which is why they are one layer here and not
two packages.

On the hot path, so stdlib only.
"""
