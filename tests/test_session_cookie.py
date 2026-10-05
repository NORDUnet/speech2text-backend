"""Backend login must not replace the UI cookie on a shared hostname."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient


class SessionTests(unittest.TestCase):
    def test_oidc_roundtrip_preserves_ui_preferences(self):
        stores = {}
        async def frontend(request):
            session = request.session.setdefault("id", str(uuid4()))
            preferences = stores.setdefault(session, {})
            if request.method == "POST":
                preferences["dark_mode"] = True
            return JSONResponse(preferences)
        async def login(request):
            request.session["oidc_state"] = "test-state"
            return JSONResponse({"ok":True})
        async def callback(request):
            return JSONResponse({"state":request.session.get("oidc_state")})
        front = Starlette(routes=[Route("/",frontend,methods=["GET","POST"])])
        front.add_middleware(SessionMiddleware, secret_key="test-ui-key")
        back = Starlette(routes=[Route("/login",login),Route("/callback",callback)])
        tree = ast.parse((Path(__file__).parents[1] / "app.py").read_text())
        middleware = next(n for n in tree.body if isinstance(n, ast.Expr) and "app.add_middleware(" in ast.unparse(n) and "SessionMiddleware" in ast.unparse(n))
        exec(compile(ast.Module(body=[middleware],type_ignores=[]),"app.py","exec"),dict(app=back,SessionMiddleware=SessionMiddleware,settings=SimpleNamespace(API_SECRET_KEY="test-backend-key")))
        app = Starlette(routes=[Mount("/api",app=back),Mount("/",app=front)])
        with TestClient(app) as client:
            client.post("/")
            cookie = client.cookies.get("session")
            for _ in range(2):
                client.get("/api/login")
                self.assertEqual(client.cookies.get("session"), cookie)
                self.assertEqual(client.get("/api/callback").json()["state"], "test-state")
                self.assertEqual(client.get("/").json(), {"dark_mode":True})
