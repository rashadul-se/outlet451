"""Admin tab: backend endpoints plus the /admin page.

Register it in create_app by passing in your existing login decorator:

    from .admin_routes import make_admin_blueprint
    from .routes import login_required          # whatever yours is called
    app.register_blueprint(make_admin_blueprint(login_required))
"""
import json
import os

from flask import Blueprint, jsonify, request, send_from_directory
from sqlalchemy import func

from . import db
from .models import User
from .rbac import ADMIN, CUSTOMER, FEATURES, NAME_RE, Role, effective_perms, require


def _label(u) -> str:
    return getattr(u, "name", None) or getattr(u, "email", None) or getattr(u, "username", None) or f"user {u.id}"


def _clean_perms(raw):
    """Return a set of valid feature keys, or None if the input is invalid."""
    if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
        return None
    perms = set(raw)
    return perms if perms <= set(FEATURES) else None


def make_admin_blueprint(login_required):
    bp = Blueprint("admin", __name__)

    @bp.get("/admin")
    def admin_page():
        # The page holds no data; every API call below enforces permissions.
        return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "admin.html")

    # ---- features (read-only catalog, with the roles that hold each one) ----
    @bp.get("/api/admin/features")
    @login_required
    @require("role:manage")
    def features(u):
        roles = Role.query.order_by(Role.name).all()
        groups = {}
        for key, (group, label) in FEATURES.items():
            held_by = [r.name for r in roles if key in effective_perms(r)]
            groups.setdefault(group, []).append({"key": key, "label": label, "roles": held_by})
        return jsonify([{"group": g, "features": f} for g, f in groups.items()])

    # ---- roles ----
    @bp.get("/api/admin/roles")
    @login_required
    @require("role:manage")
    def list_roles(u):
        counts = dict(db.session.query(User.role, func.count(User.id)).group_by(User.role).all())
        out = []
        for r in Role.query.order_by(Role.builtin.desc(), Role.name).all():
            n = counts.get(r.name, 0) + (counts.get(None, 0) if r.name == CUSTOMER else 0)
            out.append({"name": r.name, "builtin": r.builtin, "users": n,
                        "permissions": sorted(effective_perms(r))})
        return jsonify(out)

    @bp.post("/api/admin/roles")
    @login_required
    @require("role:manage")
    def create_role(u):
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip().lower()
        perms = _clean_perms(data.get("permissions", []))
        if not NAME_RE.match(name):
            return jsonify(error="Name must be 2-30 characters: letters, digits, - or _, starting with a letter"), 400
        if perms is None:
            return jsonify(error="Unknown feature in permissions"), 400
        if db.session.get(Role, name):
            return jsonify(error="That role already exists"), 409
        db.session.add(Role(name=name, permissions=json.dumps(sorted(perms)), builtin=False))
        db.session.commit()
        return jsonify(name=name, permissions=sorted(perms)), 201

    @bp.put("/api/admin/roles/<name>")
    @login_required
    @require("role:manage")
    def update_role(u, name):
        role = db.session.get(Role, name)
        if not role:
            return jsonify(error="Role not found"), 404
        if name == ADMIN:
            return jsonify(error="The admin role always has every feature"), 400
        perms = _clean_perms((request.get_json(silent=True) or {}).get("permissions"))
        if perms is None:
            return jsonify(error="Unknown feature in permissions"), 400
        role.permissions = json.dumps(sorted(perms))
        db.session.commit()
        return jsonify(name=name, permissions=sorted(perms))

    @bp.delete("/api/admin/roles/<name>")
    @login_required
    @require("role:manage")
    def delete_role(u, name):
        role = db.session.get(Role, name)
        if not role:
            return jsonify(error="Role not found"), 404
        if role.builtin:
            return jsonify(error="Built-in roles cannot be deleted"), 400
        if User.query.filter_by(role=name).count():
            return jsonify(error="Reassign the users with this role first"), 409
        db.session.delete(role)
        db.session.commit()
        return jsonify(deleted=name)

    # ---- users ----
    @bp.get("/api/admin/users")
    @login_required
    @require("user:manage")
    def list_users(u):
        return jsonify([{"id": x.id, "name": _label(x), "role": x.role or CUSTOMER}
                        for x in User.query.order_by(User.id)])

    @bp.patch("/api/admin/users/<int:uid>/role")
    @login_required
    @require("user:manage")
    def set_user_role(u, uid):
        role = (request.get_json(silent=True) or {}).get("role")
        if not isinstance(role, str) or not db.session.get(Role, role):
            return jsonify(error="Unknown role"), 400
        target = db.session.get(User, uid)
        if not target:
            return jsonify(error="User not found"), 404
        if target.role == ADMIN and role != ADMIN:
            if target.id == u.id:
                return jsonify(error="You cannot remove your own admin role"), 400
            if User.query.filter_by(role=ADMIN).count() <= 1:
                return jsonify(error="At least one admin is required"), 400
        target.role = role
        db.session.commit()
        return jsonify(id=target.id, role=target.role)

    return bp
