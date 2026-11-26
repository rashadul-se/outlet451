"""Role-based access control for Outlet 451, with roles stored in the database.

FEATURES is the catalog of things a role may be allowed to do. Roles are rows in
the rbac_role table: each holds a list of feature keys. The built-in roles are
seeded on startup; admins can create more from the /admin page.

Usage in routes.py (put @require BELOW your existing login decorator, which must
pass the user object as the first argument):

    @api.post("/orders/<int:oid>/advance")
    @login_required              # your existing auth decorator
    @require("order:advance")
    def advance(u, oid): ...
"""
import json
import re
from functools import wraps

from flask import g, jsonify
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from . import db

CUSTOMER, KITCHEN, CASHIER, MANAGER, ADMIN = "customer", "kitchen", "cashier", "manager", "admin"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,29}$")

# key -> (group, label). Insertion order is the display order.
FEATURES = {
    "menu:read":       ("Menu", "View the menu"),
    "menu:write":      ("Menu", "Edit menu items and prices"),
    "menu:import":     ("Menu", "Import the menu from CSV"),
    "menu:restock":    ("Menu", "Restock items"),
    "order:create":    ("Orders", "Place orders"),
    "order:own":       ("Orders", "View, edit and cancel their own orders"),
    "order:view_all":  ("Orders", "View every order"),
    "order:advance":   ("Orders", "Move orders to preparing, ready and completed"),
    "report:view":     ("Reports and finance", "View the daily sales report"),
    "finance:view":    ("Reports and finance", "View income statement, cash flow and balance sheet"),
    "expense:write":   ("Reports and finance", "Record expenses"),
    "payroll:run":     ("People and access", "Run monthly payroll"),
    "employee:manage": ("People and access", "Manage employees"),
    "user:manage":     ("People and access", "Assign roles to users"),
    "role:manage":     ("People and access", "Create and edit roles"),
}

_CUSTOMER = {"menu:read", "order:create", "order:own"}
_KITCHEN = {"menu:read", "order:view_all", "order:advance"}
_CASHIER = _KITCHEN | {"order:create", "order:own", "menu:restock"}
_MANAGER = _CASHIER | {"menu:write", "menu:import", "report:view", "expense:write"}

DEFAULT_ROLES = {
    CUSTOMER: frozenset(_CUSTOMER),
    KITCHEN: frozenset(_KITCHEN),
    CASHIER: frozenset(_CASHIER),
    MANAGER: frozenset(_MANAGER),
    ADMIN: frozenset(FEATURES),
}


class Role(db.Model):
    __tablename__ = "rbac_role"
    name = db.Column(db.String(30), primary_key=True)
    permissions = db.Column(db.Text, nullable=False, default="[]")  # JSON list of feature keys
    builtin = db.Column(db.Boolean, nullable=False, default=False)

    def perms(self) -> set:
        try:
            return set(json.loads(self.permissions or "[]"))
        except ValueError:
            return set()


def effective_perms(role: Role) -> frozenset:
    """Admin always holds every feature, whatever is stored."""
    if role.name == ADMIN:
        return frozenset(FEATURES)
    return frozenset(role.perms() & set(FEATURES))


def ensure_roles() -> None:
    """Create the table if needed and seed the built-in roles. Safe to call on every boot."""
    try:
        Role.__table__.create(db.engine, checkfirst=True)
        for name, perms in DEFAULT_ROLES.items():
            if not db.session.get(Role, name):
                db.session.add(Role(name=name, permissions=json.dumps(sorted(perms)), builtin=True))
        db.session.commit()
    except (IntegrityError, OperationalError, ProgrammingError):
        db.session.rollback()  # another worker won the race; the data is there


def role_of(user) -> str:
    role = getattr(user, "role", None)
    if role:
        return role
    return ADMIN if getattr(user, "is_admin", False) else CUSTOMER


def role_perms(name: str) -> frozenset:
    if name == ADMIN:
        return frozenset(FEATURES)
    cache = g.__dict__.setdefault("_rbac_cache", {})  # one lookup per role per request
    if name not in cache:
        row = db.session.get(Role, name)
        granted = row.perms() if row else set(DEFAULT_ROLES.get(name, ()))
        cache[name] = frozenset(granted & set(FEATURES))
    return cache[name]


def has_perm(user, *perms: str) -> bool:
    granted = role_perms(role_of(user))
    return all(p in granted for p in perms)


def require(*perms: str):
    """Decorator for view functions shaped like fn(u, *args, **kwargs)."""
    def deco(fn):
        @wraps(fn)
        def inner(u, *a, **k):
            if not has_perm(u, *perms):
                return jsonify(error="Forbidden"), 403
            return fn(u, *a, **k)
        return inner
    return deco
