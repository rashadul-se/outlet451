"""Business logic lives here; routes never touch pricing, stock or points rules directly."""
import csv, io, os, smtplib, threading
from datetime import datetime, timedelta, date
from decimal import Decimal
from email.message import EmailMessage
from flask import current_app
from sqlalchemy import func
from . import db, cache
from .models import MenuItem, Order, OrderItem, Expense, User, Employee, Payroll

TAX_RATE, POINT_VALUE = Decimal("0.05"), Decimal("0.05")   # 1 point = $0.05; earn 1 point per $1 paid
EDIT_WINDOW = timedelta(seconds=int(os.getenv("EDIT_WINDOW_SECONDS", 300)))
AUDIT_EMAIL = os.getenv("AUDIT_EMAIL", "contact-project+gallery-rasha-ross-clientservice-89481018-issue-@incoming.gitlab.com")
METHODS = {"cash", "card", "bkash"}
FLOW = {"placed": "preparing", "preparing": "ready", "ready": "completed"}
q2 = lambda d: Decimal(d).quantize(Decimal("0.01"))


class ServiceError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


class PaymentGateway:
    """Stub. Swap in real card (tokenised, never raw PAN) and bKash API calls here."""
    def charge(self, method, amount, token=None):
        if method not in METHODS:
            raise ServiceError("Invalid payment method")
        if method in ("card", "bkash") and not token:
            raise ServiceError("Payment token / bKash transaction id required")
        return f"{method}-{int(datetime.utcnow().timestamp() * 1000)}"

    def adjust(self, order, delta):
        return f"{order.payment_method}-adj-{int(datetime.utcnow().timestamp() * 1000)}"


class MenuService:
    KEY = "menu:active"

    def active(self):
        data = cache.get(self.KEY)
        if data is None:
            data = [i.to_dict() for i in MenuItem.query.filter_by(active=True).order_by(MenuItem.category, MenuItem.name)]
            cache.set(self.KEY, data)
        return data

    def invalidate(self):
        cache.delete(self.KEY)

    def import_csv(self, text):
        n = 0
        for r in csv.DictReader(io.StringIO(text)):
            m = MenuItem.query.filter_by(name=r["name"].strip()).first() or MenuItem(name=r["name"].strip())
            m.category, m.description = r["category"].strip(), r.get("description", "")
            m.price, m.discount_pct, m.stock = Decimal(r["price"]), int(r.get("discount_pct") or 0), int(r.get("stock") or 0)
            db.session.add(m)
            n += 1
        db.session.commit()
        self.invalidate()
        return n

    def restock(self, item_id, qty):
        m = db.session.get(MenuItem, item_id) or _fail("Item not found", 404)
        m.stock = max(0, m.stock + qty)
        db.session.commit()
        self.invalidate()
        return m.stock


def _fail(msg, code=400):
    raise ServiceError(msg, code)


class OrderService:
    def __init__(self, gateway=None):
        self._gw = gateway or PaymentGateway()

    # ---- public API ----
    def place(self, user, lines, method, token=None, redeem=0):
        order = Order(user_id=user.id, payment_method=method)
        db.session.add(order)
        self._fill(order, lines)
        self._totals(order, min(int(redeem or 0), user.points))
        user.points -= order.points_used
        order.payment_ref = self._gw.charge(method, order.total, token)
        order.points_earned = int(order.total)
        user.points += order.points_earned
        db.session.commit()
        self._ticket(order, "PLACED")
        return order

    def update(self, user, order_id, lines):
        order = self._editable(user, order_id)
        old_total = order.total
        self._release(order)
        self._fill(order, lines)
        self._totals(order, order.points_used)
        user.points += int(order.total) - order.points_earned
        order.points_earned = int(order.total)
        if order.total != old_total:
            order.payment_ref = self._gw.adjust(order, order.total - old_total)
        db.session.commit()
        self._ticket(order, "UPDATED")
        return order

    def cancel(self, user, order_id):
        order = self._editable(user, order_id)
        self._release(order)
        user.points = max(0, user.points + order.points_used - order.points_earned)
        order.status = "cancelled"
        self._gw.adjust(order, -order.total)
        db.session.commit()
        self._ticket(order, "CANCELLED")
        return order

    def advance(self, order_id):
        order = db.session.get(Order, order_id) or _fail("Order not found", 404)
        order.status = FLOW.get(order.status) or _fail("Order cannot advance", 409)
        db.session.commit()
        return order

    # ---- private helpers (encapsulated rules) ----
    def _editable(self, user, order_id):
        order = db.session.get(Order, order_id, with_for_update=True)
        if not order or order.user_id != user.id:
            _fail("Order not found", 404)
        if order.status != "placed" or datetime.utcnow() - order.created_at > EDIT_WINDOW:
            _fail("Edit window closed", 409)
        return order

    def _fill(self, order, lines):
        qty = {}
        for l in lines or []:
            n = int(l.get("qty", 0))
            if not 0 < n <= 20:
                _fail("Quantity must be 1-20")
            qty[int(l["item_id"])] = qty.get(int(l["item_id"]), 0) + n
        if not qty:
            _fail("Cart is empty")
        items = MenuItem.query.filter(MenuItem.id.in_(sorted(qty))).order_by(MenuItem.id).with_for_update().all()
        if len(items) != len(qty):
            _fail("Unknown item")
        subtotal = Decimal(0)
        for it in items:
            if not it.active or it.stock < qty[it.id]:
                _fail(f"{it.name} is unavailable", 409)
            it.stock -= qty[it.id]
            order.items.append(OrderItem(item_id=it.id, qty=qty[it.id], unit_price=it.final_price))
            subtotal += it.final_price * qty[it.id]
        order.subtotal = q2(subtotal)

    def _totals(self, order, points):
        order.tax = q2(order.subtotal * TAX_RATE)
        gross = order.subtotal + order.tax
        order.points_used = max(0, min(points, int(gross / POINT_VALUE)))
        order.total = q2(gross - order.points_used * POINT_VALUE)

    def _release(self, order):
        for oi in list(order.items):
            m = db.session.get(MenuItem, oi.item_id, with_for_update=True)
            m.stock += oi.qty
        order.items.clear()
        db.session.flush()

    def _ticket(self, order, event):
        """Audit ticket: order data only, no names, emails or payment tokens."""
        lines = "\n".join(f"{i.qty} x {i.item.name} @ {i.unit_price}" for i in order.items)
        body = (f"Order #{order.id} {event}\nCustomer id: {order.user_id}\nMethod: {order.payment_method}\n"
                f"{lines}\nSubtotal {order.subtotal} Tax {order.tax} Points used {order.points_used}\nTotal {order.total}\n")
        host = os.getenv("SMTP_HOST")
        if not host:
            current_app.logger.info("AUDIT TICKET (SMTP not configured)\n%s", body)
            return
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = f"[Outlet451] Order #{order.id} {event}", os.getenv("SMTP_FROM", "orders@outlet451.local"), AUDIT_EMAIL
        msg.set_content(body)
        app = current_app._get_current_object()
        threading.Thread(target=self._send, args=(app, host, msg), daemon=True).start()

    @staticmethod
    def _send(app, host, msg):
        try:
            with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", 587)), timeout=15) as s:
                s.starttls()
                if os.getenv("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                s.send_message(msg)
        except Exception:
            app.logger.exception("audit ticket email failed")


class ReportService:
    def daily(self, day=None):
        day = day or date.today()
        start, end = datetime.combine(day, datetime.min.time()), datetime.combine(day + timedelta(days=1), datetime.min.time())
        live = (Order.created_at >= start, Order.created_at < end, Order.status != "cancelled")
        income, tax, count = db.session.query(func.coalesce(func.sum(Order.total), 0), func.coalesce(func.sum(Order.tax), 0), func.count(Order.id)).filter(*live).one()
        spend = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(Expense.day == day).scalar()
        top = (db.session.query(MenuItem.name, func.sum(OrderItem.qty)).join(OrderItem, OrderItem.item_id == MenuItem.id)
               .join(Order, Order.id == OrderItem.order_id).filter(*live).group_by(MenuItem.name)
               .order_by(func.sum(OrderItem.qty).desc()).limit(5).all())
        net = income - tax - spend
        return dict(date=str(day), orders=count, income=float(income), tax_collected=float(tax), expenses=float(spend),
                    net=float(net), result="profit" if net >= 0 else "loss", top_items=[[n, int(q)] for n, q in top])


class PayrollService:
    """Monthly run is idempotent per employee+month. Gross is booked as a 'payroll' expense
    (withholding tax is assumed remitted with the run; refine if you remit separately)."""
    def run(self, month):
        datetime.strptime(month, "%Y-%m")
        paid, total = 0, Decimal(0)
        for e in Employee.query.filter_by(active=True):
            if Payroll.query.filter_by(employee_id=e.id, month=month).first():
                continue
            gross = q2(e.monthly_salary)
            tax = q2(gross * e.tax_rate)
            db.session.add(Payroll(employee_id=e.id, month=month, gross=gross, tax=tax, net=gross - tax))
            total += gross
            paid += 1
        if total:
            db.session.add(Expense(category="payroll", amount=total, note=f"Payroll {month}"))
        db.session.commit()
        return dict(month=month, employees_paid=paid, gross_total=float(total))


class FinanceService:
    """Simplified accrual-free statements derived from orders and expenses."""
    @staticmethod
    def _live(start, end):
        return (Order.created_at >= datetime.combine(start, datetime.min.time()),
                Order.created_at < datetime.combine(end + timedelta(days=1), datetime.min.time()),
                Order.status != "cancelled")

    @staticmethod
    def _sum(col, *flt):
        return db.session.query(func.coalesce(func.sum(col), 0)).filter(*flt).scalar()

    def _expenses(self, start, end):
        rows = (db.session.query(Expense.category, func.sum(Expense.amount))
                .filter(Expense.day >= start, Expense.day <= end).group_by(Expense.category).all())
        return {c: float(a) for c, a in rows}

    def income_statement(self, start, end):
        live = self._live(start, end)
        sub, tax, tot, pts = (self._sum(c, *live) for c in (Order.subtotal, Order.tax, Order.total, Order.points_used))
        exp = {c: a for c, a in self._expenses(start, end).items() if c != "tax_remittance"}
        revenue, spend = float(tot - tax), sum(exp.values())
        return dict(period=f"{start} to {end}", gross_sales=float(sub), points_redeemed_value=float(pts * POINT_VALUE),
                    net_revenue=revenue, expenses=exp, total_expenses=spend, net_income=round(revenue - spend, 2),
                    sales_tax_collected=float(tax))

    def cash_flow(self, start, end):
        inflow = float(self._sum(Order.total, *self._live(start, end)))
        out = self._expenses(start, end)
        return dict(period=f"{start} to {end}", cash_from_sales=inflow, outflows=out,
                    total_outflow=sum(out.values()), net_cash_change=round(inflow - sum(out.values()), 2))

    def balance_sheet(self, asof):
        live = self._live(date(2000, 1, 1), asof)
        inflow, tax = self._sum(Order.total, *live), self._sum(Order.tax, *live)
        spent = self._sum(Expense.amount, Expense.day <= asof)
        remitted = self._sum(Expense.amount, Expense.day <= asof, Expense.category == "tax_remittance")
        loyalty = float(self._sum(User.points) * POINT_VALUE)   # current balance, not historical
        cash, tax_payable = float(inflow - spent), float(tax - remitted)
        return dict(as_of=str(asof), assets=dict(cash=cash, total=cash),
                    liabilities=dict(sales_tax_payable=tax_payable, loyalty_points=loyalty, total=round(tax_payable + loyalty, 2)),
                    equity=round(cash - tax_payable - loyalty, 2))
