"""
Shared pytest wiring.

ONE job today: the API tests predate authentication. They build a
TestClient over the routes and call them directly, and all 28 of those
routes are now gated by app/routes.py's router-level
Depends(require_user). Rather than teach eight test files to log in —
which would test the login, not the route — every FastAPI app created
during a test run starts with that dependency overridden by a fixed
user. dependency_overrides is the standard FastAPI seam for exactly
this.

Why patch FastAPI.__init__ rather than write a fixture: overrides live
on an app INSTANCE, and the test suite builds several — app.main.app at
module import, plus a bare FastAPI() per module-scoped `client` fixture
(the bare-app pattern exists so importing app.main, and with it
configure_logging(), never happens under pytest). Those are created at
import and fixture-setup time, both of which run before a function-scoped
fixture could reach them. Patching the constructor catches every one,
whenever it is built.

The import of app.auth is deferred into the patched constructor so a
pure-engine run (golden master, parsers) still never imports the web
layer at all.

The gate ITSELF is tested for real — real cookies, no override — in
tests/test_auth.py, which clears the override on its own app. Keep that:
if this file were the only place authentication appeared, a router that
lost its gate would still look green.
"""

import fastapi

TEST_USER_ID = 0
TEST_USER_EMAIL = "test@example.com"

_orig_init = fastapi.FastAPI.__init__


def _init_with_test_user(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    # deferred: an engine-only test run never imports app.* at all
    from app.auth import AuthUser, require_user
    user = AuthUser(id=TEST_USER_ID, email=TEST_USER_EMAIL, name="Test User")
    self.dependency_overrides[require_user] = lambda: user


fastapi.FastAPI.__init__ = _init_with_test_user
