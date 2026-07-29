"""Route groups, lifted out of ``create_app``.

``create_app`` held 155 handlers in one 2,693-line function, each closing
over whichever of its 80-odd service objects it happened to need. The
closure made those couplings invisible; every ``register`` here states them
in one signature instead.

The split is by surface, not by size: what a caller is asking for is a
better boundary than how many lines it takes to answer.
"""

from __future__ import annotations

from . import (  # noqa: F401
    agent_platform,
    execution_routes,
    knowledge_routes,
    market_data_routes,
    operations_routes,
    platform_routes,
    research_routes,
)
