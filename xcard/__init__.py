"""ComfyFlash Expansion Card directory discovery.

Every immediate tool directory under xcard/ is discovered automatically.
The host does not impose a per-tool manifest or configuration format.
"""

from .catalog import XCardCatalog, XCardError

__all__ = ["XCardCatalog", "XCardError"]
