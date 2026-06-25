"""ApexMind tools package — the deterministic 'hands' of the agent.

The Python layer never reasons. It fetches Polymarket data, reads/writes
file-based memory, scores predictions, runs research, and assembles briefings for
Claude Code (the 'brain') to reason over.
"""

# Re-export the research instruments for ergonomic access:
#   from tools import web_search, x_search, browse_page
from tools.research import (  # noqa: F401
    browse_page,
    crypto_onchain,
    gather,
    polling_search,
    web_search,
    x_search,
)

__all__ = ["web_search", "x_search", "browse_page", "gather",
           "crypto_onchain", "polling_search"]
