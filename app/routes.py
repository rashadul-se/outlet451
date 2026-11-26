from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from flask import Blueprint, jsonify, request, session, render_template
from . import db
from .models import User, Order, Expense, MenuItem
from .services import (MenuService, OrderService, ReportService, ServiceError, EDIT_WINDOW)

api = Blueprint("api", __name__, url_prefix="/api")
auth = Blueprint("auth", __name__, url_prefix="/api")
pages = Blueprint("pages", __name__)
menu_svc, order_svc, report_svc = MenuService(), OrderService(), ReportService()


@api.errorhandler(ServiceError)
def _svc_err(e):
    return jsonify(error=str(e)), e.code


@api.errorhandler(ValueError)
def _val_err(e):
    return jsonify(error="Invalid input"), 400


def current_user():
    return db.session.get(User, session["uid"]) if "uid" in session else None


def role_required(*roles):
    """RBAC: no roles = any signed-in user."""
    def deco(fn):
        @wraps(fn)
        def inner(*a, **k):
            u = current_user()
            if not u:
                return jsonify(error="Login required"), 401
            if roles and u.role not in roles and u.role != "admin":
                return jsonify(error="Forbidden"), 403
            return fn(u, *a, **k)
        return inner
    return deco


def order_dict(o):
    left = max(0, int((EDIT_WINDOW - (datetime.utcnow() - o.created_at)).total_seconds())) if o.status == "placed" else 0
    return dict(id=o.id, status=o.status, subtotal=float(o.subtotal), tax=float(o.tax), points_used=o.points_used,
                total=float(o.total), method=o.payment_method, edit_seconds_left=left,
                items=[dict(item_id=i.item_id, name=i.item.name, qty=i.qty, price=float(i.unit_price)) for i in o.items])


@pages.get("/")
def home():
    return render_template("index.html")


@pages.get("/healthz")
def health():
    return "ok"


# ---- auth ----
@auth.post("/register")
def register():
    d = request.get_json(force=True)
    if len(d.get("password", "")) < 8 or "@" not in d.get("email", ""):
        return jsonify(error="Valid email and 8+ char password required"), 400
    if User.query.filter_by(email=d["email"].lower()).first():
        return jsonify(error="Email already registered"), 409
    u = User(name=d.get("name", "Guest")[:80], email=d["email"].lower())
    u.set_password(d["password"])
    db.session.add(u)
    db.session.commit()
    session["uid"] = u.id
    return jsonify(name=u.name, role=u.role, points=u.points), 201


@auth.post("/login")
def login():
    d = request.get_json(force=True)
    u = User.query.filter_by(email=d.get("email", "").lower()).first()
    if not u or not u.check_password(d.get("password", "")):
        return jsonify(error="Invalid credentials"), 401
    session["uid"] = u.id
    return jsonify(name=u.name, role=u.role, points=u.points)


@auth.post("/logout")
def logout():
    session.clear()
    return "", 204


@auth.get("/me")
def me():
    u = current_user()
    return jsonify(name=u.name, role=u.role, points=u.points) if u else (jsonify(error="anon"), 401)


# ---- customer ----
@api.get("/menu")
def menu():
    return jsonify(menu_svc.active())


@api.post("/orders")
@role_required()
def checkout(u):
    d = request.get_json(force=True)
    o = order_svc.place(u, d.get("items"), d.get("method"), d.get("token"), d.get("redeem_points", 0))
    return jsonify({**order_dict(o), "points": u.points}), 201
    


@api.get("/orders")
@role_required()
def my_orders(u):
    rows = Order.query.filter_by(user_id=u.id).order_by(Order.id.desc()).limit(20).all()
    return jsonify([order_dict(o) for o in rows])


@api.put("/orders/<int:oid>")
@role_required()
def edit_order(u, oid):
    return jsonify(order_dict(order_svc.update(u, oid, request.get_json(force=True).get("items"))))


@api.delete("/orders/<int:oid>")
@role_required()
def cancel_order(u, oid):
    return jsonify(order_dict(order_svc.cancel(u, oid)))



@api.get("/orders/<int:oid>/receipt")
@role_required()
def receipt(u, oid):
    o = db.session.get(Order, oid)
    if not o or (o.user_id != u.id and u.role not in ("admin", "cashier")):
        return jsonify(error="Not found"), 404
    return render_template("receipt.html", o=o)
