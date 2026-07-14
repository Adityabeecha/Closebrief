"""Shared FastAPI auth dependencies.

Split out of main.py so route modules (app/routers/*) can import the role guards
without a circular dependency on main. main.py re-imports these too, so there is
one definition of each guard.
"""

from app.auth import (  # noqa: F401  (CurrentUser/get_current_user re-exported for routers)
    CurrentUser,
    get_current_user,
    require_role,
)

# Role-guard dependencies (v1.2). read = any authenticated role; write excludes
# executives (read-only); admin only for user management; member = signed-in
# non-demo (gates paid LLM calls + real-report mutations).
require_read = require_role("viewer", "analyst", "executive", "admin")
require_write = require_role("analyst", "admin")            # viewer excluded -> demo writes 403
require_member = require_role("analyst", "executive", "admin")
require_admin = require_role("admin")
