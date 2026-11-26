"""Staff console: /staff page plus the role-gated endpoints behind it.

Register next to the admin blueprint in create_app:

    from .staff_routes import make_staff_blueprint
    app.register_blueprint(make_staff_blueprint(login_required))
"""
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, jsonify, request, send_from_directory

from . import db
from .admin_routes import _label
from .models import Employee, Expense, MenuItem, Order
from .rbac import has_perm, require, role_of, role_perms
from .services import (FinanceService, MenuService, OrderService, PayrollService,
                       ReportService, ServiceError)


def _guard(fn):
    """Turn service errors and bad input into clean JSON instead of an HTML 500."""
    @wraps(fn)
    def inner(*a, **k):
        try:
            return fn(*a, **k)
        except ServiceError as e:
            return jsonify(error=str(e)), e.code
        except (ValueError, InvalidOperation, KeyError, TypeError) as e:
            db.session.rollback()
            return jsonify(error=f"Invalid input: {e}"), 400
    return inner


def _day(raw, default):
    return date.fromisoformat(raw) if raw else default


def _order(o):
    return {"id": o.id, "status": o.status, "customer": o.user_id, "method": o.payment_method,
            "total": float(o.total), "created_at": o.created_at.isoformat() + "Z" if o.created_at else None,
            "items": [{"name": i.item.name, "qty": i.qty} for i in o.items]}


def make_staff_blueprint(login_required):
    bp = Blueprint("staff", __name__)

    @bp.get("/staff")
    def staff_page():
        # No data in the page itself; every endpoint below checks permissions.
        return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "staff.html")

    @bp.get("/api/me")
    @login_required
    def me(u):
        return jsonify(id=u.id, name=_label(u), role=role_of(u), points=getattr(u, "points", 0),
                       permissions=sorted(role_perms(role_of(u))))

    # ---- orders (kitchen and up) ----
    @bp.get("/api/staff/orders")
    @login_required
    @require("order:view_all")
    def orders(u):
        rows = Order.query.filter(Order.status != "cancelled").order_by(Order.id.desc()).limit(80).all()
        return jsonify([_order(o) for o in rows])

    @bp.post("/api/staff/orders/<int:oid>/advance")
    @login_required
    @require("order:advance")
    @_guard
    def advance(u, oid):
        return jsonify(_order(OrderService().advance(oid)))

    # ---- menu and stock ----
    @bp.get("/api/staff/menu")
    @login_required
    @_guard
    def menu(u):
        if not (has_perm(u, "menu:restock") or has_perm(u, "menu:write")):
            return jsonify(error="Forbidden"), 403
        rows = MenuItem.query.order_by(MenuItem.category, MenuItem.name).all()
        return jsonify([{"id": m.id, "name": m.name, "category": m.category, "price": float(m.price),
                         "discount_pct": m.discount_pct, "stock": m.stock, "active": bool(m.active)} for m in rows])

    @bp.post("/api/staff/menu/<int:item_id>/restock")
    @login_required
    @require("menu:restock")
    @_guard
    def restock(u, item_id):
        qty = int((request.get_json(silent=True) or {}).get("qty"))
        if not -1000 <= qty <= 1000:
            raise ServiceError("Quantity must be between -1000 and 1000")
        return jsonify(stock=MenuService().restock(item_id, qty))

    @bp.patch("/api/staff/menu/<int:item_id>")
    @login_required
    @require("menu:write")
    @_guard
    def edit_item(u, item_id):
        data = request.get_json(silent=True) or {}
        m = db.session.get(MenuItem, item_id)
        if not m:
            raise ServiceError("Item not found", 404)
        if "price" in data:
            price = Decimal(str(data["price"]))
            if price < 0:
                raise ServiceError("Price cannot be negative")
            m.price = price
        if "discount_pct" in data:
            d = int(data["discount_pct"])
            if not 0 <= d <= 100:
                raise ServiceError("Discount must be 0-100")
            m.discount_pct = d
        if "active" in data:
            m.active = bool(data["active"])
        db.session.commit()
        MenuService().invalidate()
        return jsonify(id=m.id)

    @bp.post("/api/staff/menu/import")
    @login_required
    @require("menu:import")
    @_guard
    def import_menu(u):
        text = (request.get_json(silent=True) or {}).get("csv", "")
        if not text.strip():
            raise ServiceError("Paste CSV text first")
        return jsonify(imported=MenuService().import_csv(text))

    # ---- reports and expenses (manager and up) ----
    @bp.get("/api/staff/reports/daily")
    @login_required
    @require("report:view")
    @_guard
    def daily(u):
        return jsonify(ReportService().daily(_day(request.args.get("date"), date.today())))

    @bp.get("/api/staff/expenses")
    @login_required
    @require("expense:write")
    def list_expenses(u):
        rows = Expense.query.order_by(Expense.id.desc()).limit(10).all()
        return jsonify([{"day": str(e.day), "category": e.category, "amount": float(e.amount),
                         "note": getattr(e, "note", "") or ""} for e in rows])

    @bp.post("/api/staff/expenses")
    @login_required
    @require("expense:write")
    @_guard
    def add_expense(u):
        d = request.get_json(silent=True) or {}
        category = str(d.get("category", "")).strip().lower()
        amount = Decimal(str(d.get("amount")))
        if not category or len(category) > 40:
            raise ServiceError("Category is required (max 40 characters)")
        if amount <= 0:
            raise ServiceError("Amount must be above zero")
        db.session.add(Expense(category=category, amount=amount, note=str(d.get("note", ""))[:200],
                               day=_day(d.get("day"), date.today())))
        db.session.commit()
        return jsonify(ok=True), 201

    # ---- finance, payroll, employees (admin) ----
    @bp.get("/api/staff/finance")
    @login_required
    @require("finance:view")
    @_guard
    def finance(u):
        today = date.today()
        start, end = _day(request.args.get("start"), today.replace(day=1)), _day(request.args.get("end"), today)
        if start > end:
            raise ServiceError("Start date is after end date")
        f = FinanceService()
        return jsonify(income_statement=f.income_statement(start, end), cash_flow=f.cash_flow(start, end),
                       balance_sheet=f.balance_sheet(end))

    @bp.post("/api/staff/payroll/run")
    @login_required
    @require("payroll:run")
    @_guard
    def run_payroll(u):
        return jsonify(PayrollService().run(str((request.get_json(silent=True) or {}).get("month", ""))))

    @bp.get("/api/staff/employees")
    @login_required
    @require("employee:manage")
    def employees(u):
        return jsonify([{"id": e.id, "name": getattr(e, "name", None) or f"Employee {e.id}", "active": bool(e.active),
                         "monthly_salary": float(e.monthly_salary), "tax_rate": float(e.tax_rate)}
                        for e in Employee.query.order_by(Employee.id)])

    @bp.patch("/api/staff/employees/<int:eid>")
    @login_required
    @require("employee:manage")
    @_guard
    def edit_employee(u, eid):
        data = request.get_json(silent=True) or {}
        e = db.session.get(Employee, eid)
        if not e:
            raise ServiceError("Employee not found", 404)
        if "monthly_salary" in data:
            salary = Decimal(str(data["monthly_salary"]))
            if salary < 0:
                raise ServiceError("Salary cannot be negative")
            e.monthly_salary = salary
        if "tax_rate" in data:
            rate = Decimal(str(data["tax_rate"]))
            if not 0 <= rate <= 1:
                raise ServiceError("Tax rate must be between 0 and 1 (0.10 = 10%)")
            e.tax_rate = rate
        if "active" in data:
            e.active = bool(data["active"])
        db.session.commit()
        return jsonify(id=e.id)

    return bp
