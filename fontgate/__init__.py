"""fontgate — verification gates for web font delivery.

The public surface is small on purpose: the package answers one question —
*is this font file safe to ship?* — and answers it with measurements taken
from the binary, not from documentation.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
