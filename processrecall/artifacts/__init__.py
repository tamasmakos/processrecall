"""artifacts — what the agent's edits actually touched, in source terms.

Attributes a file edit to the enclosing symbol so a step can be about a function
rather than a line range. Alone among the new layers this one parses source with
tree-sitter, so it runs off the hot path only and nothing on the hot path may
import it.
"""
