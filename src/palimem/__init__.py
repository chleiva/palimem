"""palimem: justified memory for LLM agents (pre-alpha).

``from palimem import Memory`` is the public facade (see :mod:`palimem.facade`); the host-level core, the agent tool
API, the MCP server and the CLI live in :mod:`palimem.memory`, :mod:`palimem.agent`, :mod:`palimem.mcp` and
:mod:`palimem.cli`.
"""

__version__ = "0.1.0"

from palimem.facade import (
    Memory,
    Observed,
)

__all__ = ["Memory", "Observed", "__version__"]
