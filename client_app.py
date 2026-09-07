"""Desktop entry module.

The EXE now runs the same local backend as the main app. Provider calls leave the
machine only through pw_access and are authenticated with the signed-in PW user.
"""

from server import app

__all__ = ["app"]
