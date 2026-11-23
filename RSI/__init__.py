"""ComfyFlash Recursive Self-Improvement (RSI) memory.

The RSI package is deliberately local and on-demand: SQLite is opened only for
individual operations. It starts no service, daemon, worker, or background task.
"""

from .store import RSIStore, RSIStoreError

__all__ = ["RSIStore", "RSIStoreError"]
