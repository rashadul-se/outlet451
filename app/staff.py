from datetime import date
from decimal import Decimal
from flask import Blueprint, jsonify, request, render_template
from . import db
from .models import Order, MenuItem, Expense, Employee, Payroll, User, ROLES
from .routes import role_required, order_dict, menu_svc, order_svc, report_svc, _svc_err
from .services import ServiceError, PayrollService, FinanceService

staff = Blueprint("staff", __name__, url_prefix="/api")
staff_pages = Blueprint("staff_pages", __name__)
staff.register_error_handler(ServiceError, _svc_err)
for _exc in (ValueError, ArithmeticError, KeyError, TypeError):
    staff.register_error_handler(_exc, lambda e: (jsonify(error="Invalid input"), 400))
payroll_svc, fin_svc = PayrollService(), FinanceService()
_d = lambda k, default: date.fromisoformat(request.args[k]) if k in request.args else default


@staff_pages.get("/staff")
def staff_home():
    return render_template("staff.html")


# ---- kitchen ----
@staff.get("/kitchen/orders")
@role_required("kitchen")
def kitchen_queue(u):
    rows = Order.query.filter(Order.status.in_(["placed", "preparing", "ready"])).order_by(Order.id).all()
    return jsonify([order_dict(o) for o in rows])


@staff.post("/kitchen/orders/<int:oid>/advance")
@role_required("kitchen")
def kitchen_advance(u, oid):
    return jsonify(order_dict(order_svc.advance(oid)))


@staff.get("/kitchen/inventory")
@role_required("kitchen")
def inventory(u):
    return jsonify([dict(id=m.id, name=m.name, stock=m.stock, low=m.stock < 10) for m in MenuItem.query.order_by(MenuItem.stock)])


@staff.post("/kitchen/inventory/<int:iid>/restock")
@role_required("kitchen")
def restock(u, iid):
    return jsonify(stock=menu_svc.restock(iid, int(request.get_json(force=True)["qty"])))


# ---- admin ----
@staff.post("/admin/menu/import")
@role_required("admin")
def import_menu(u):
    return jsonify(imported=menu_svc.import_csv(request.get_data(as_text=True)))


@staff.patch("/admin/menu/<int:iid>")
@role_required("admin")
def edit_menu(u, iid):
    m = db.session.get(MenuItem, iid) or (None)
    if not m:
        return jsonify(error="Not found"), 404
    d = request.get_json(force=True)
    if "price" in d: m.price = Decimal(str(d["price"]))
    if "discount_pct" in d: m.discount_pct = max(0, min(90, int(d["discount_pct"])))
    if "active" in d: m.active = bool(d["active"])
    db.session.commit()
    menu_svc.invalidate()
    return jsonify(m.to_dict())


@staff.post("/admin/expenses")
@role_required("admin")
def add_expense(u):
    d = request.get_json(force=True)
    db.session.add(Expense(category=d["category"], amount=Decimal(str(d["amount"])), note=d.get("note", "")))
    db.session.commit()
    return "", 201


@staff.get("/admin/reports/daily")
@role_required("admin")
def daily_report(u):
    return jsonify(report_svc.daily(date.fromisoformat(request.args["date"]) if "date" in request.args else None))


@staff.get("/admin/reports/income-statement")
@role_required("admin")
def income_statement(u):
    t = date.today()
    return jsonify(fin_svc.income_statement(_d("from", t.replace(day=1)), _d("to", t)))


@staff.get("/admin/reports/cash-flow")
@role_required("admin")
def cash_flow(u):
    t = date.today()
    return jsonify(fin_svc.cash_flow(_d("from", t.replace(day=1)), _d("to", t)))


@staff.get("/admin/reports/balance-sheet")
@role_required("admin")
def balance_sheet(u):
    return jsonify(fin_svc.balance_sheet(_d("asof", date.today())))


@staff.post("/admin/users")
@role_required("admin")
def create_user(u):
    d = request.get_json(force=True)
    if d["role"] not in ROLES or len(d["password"]) < 8 or User.query.filter_by(email=d["email"].lower()).first():
        return jsonify(error="Invalid role/password or email already used"), 400
    n = User(name=d["name"][:80], email=d["email"].lower(), role=d["role"])
    n.set_password(d["password"])
    db.session.add(n)
    db.session.commit()
    return jsonify(id=n.id), 201


@staff.route("/admin/employees", methods=["GET", "POST"])
@role_required("admin")
def employees(u):
    if request.method == "POST":
        d = request.get_json(force=True)
        sal, rate = Decimal(str(d["salary"])), Decimal(str(d.get("tax_rate", 0.10)))
        if sal <= 0 or not 0 <= rate <= Decimal("0.5"):
            return jsonify(error="Invalid salary or tax rate"), 400
        e = Employee(name=d["name"][:80], position=d.get("position", "")[:60], monthly_salary=sal, tax_rate=rate)
        db.session.add(e)
        db.session.commit()
        return jsonify(id=e.id), 201
    return jsonify([dict(id=e.id, name=e.name, position=e.position, salary=float(e.monthly_salary), active=e.active)
                    for e in Employee.query.order_by(Employee.name)])


@staff.post("/admin/payroll/run")
@role_required("admin")
def run_payroll(u):
    return jsonify(payroll_svc.run(request.get_json(force=True)["month"]))


@staff.get("/admin/payroll")
@role_required("admin")
def payroll_list(u):
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    rows = db.session.query(Payroll, Employee.name).join(Employee).filter(Payroll.month == month).all()
    return jsonify([dict(employee=n, gross=float(p.gross), tax=float(p.tax), net=float(p.net)) for p, n in rows])
