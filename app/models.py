from datetime import datetime
from decimal import Decimal
from werkzeug.security import generate_password_hash, check_password_hash
from . import db

ROLES = ("customer", "kitchen", "cashier", "admin")


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    _pw = db.Column("password_hash", db.String(255), nullable=False)
    role = db.Column(db.String(20), default="customer", nullable=False)
    points = db.Column(db.Integer, default=0, nullable=False)

    def set_password(self, raw):
        self._pw = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self._pw, raw)


class MenuItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    category = db.Column(db.String(40), nullable=False, index=True)
    description = db.Column(db.String(200), default="")
    price = db.Column(db.Numeric(10, 2), nullable=False)
    discount_pct = db.Column(db.Integer, default=0)
    stock = db.Column(db.Integer, default=0, nullable=False)
    active = db.Column(db.Boolean, default=True)

    @property
    def final_price(self):
        return (self.price * (100 - self.discount_pct) / 100).quantize(Decimal("0.01"))

    def to_dict(self):
        return dict(id=self.id, name=self.name, category=self.category, description=self.description,
                    price=float(self.price), final_price=float(self.final_price),
                    discount_pct=self.discount_pct, available=self.stock > 0)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True, nullable=False)
    status = db.Column(db.String(20), default="placed", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    subtotal = db.Column(db.Numeric(10, 2), default=0)
    tax = db.Column(db.Numeric(10, 2), default=0)
    points_used = db.Column(db.Integer, default=0)
    points_earned = db.Column(db.Integer, default=0)
    total = db.Column(db.Numeric(10, 2), default=0)
    payment_method = db.Column(db.String(10))
    payment_ref = db.Column(db.String(60))
    items = db.relationship("OrderItem", cascade="all, delete-orphan", lazy="joined")


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), index=True, nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("menu_item.id"), nullable=False)
    qty = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    item = db.relationship("MenuItem", lazy="joined")


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, default=datetime.utcnow, index=True)
    category = db.Column(db.String(40), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    note = db.Column(db.String(200), default="")


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    position = db.Column(db.String(60), default="")
    monthly_salary = db.Column(db.Numeric(10, 2), nullable=False)
    tax_rate = db.Column(db.Numeric(4, 3), default=Decimal("0.10"))
    active = db.Column(db.Boolean, default=True)


class Payroll(db.Model):
    __table_args__ = (db.UniqueConstraint("employee_id", "month"),)
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    month = db.Column(db.String(7), nullable=False, index=True)
    gross = db.Column(db.Numeric(10, 2), nullable=False)
    tax = db.Column(db.Numeric(10, 2), nullable=False)
    net = db.Column(db.Numeric(10, 2), nullable=False)
    paid_on = db.Column(db.Date, default=datetime.utcnow)
