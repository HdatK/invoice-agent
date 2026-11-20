"""SQLAlchemy models. Plain SQLAlchemy 2.0 so swapping SQLite for another engine is a DATABASE_URL change."""
from __future__ import annotations
from datetime import datetime, date
from sqlalchemy import String, Integer, BigInteger, Float, Boolean, Date, DateTime, ForeignKey, JSON, Text, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class Visitor(Base):
    __tablename__ = "visitors"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)          # random 128-bit token
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class BusinessSettings(Base):
    __tablename__ = "business_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    visitor_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(200))
    economic_code: Mapped[str | None] = mapped_column(String(20))
    national_id: Mapped[str | None] = mapped_column(String(20))
    default_currency: Mapped[str] = mapped_column(String(3), default="IRT")
    vat_rate: Mapped[float] = mapped_column(Float, default=0.10)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name_fa: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(10), default="expense")       # expense | income | both
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)


class SourceFile(Base):
    __tablename__ = "source_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    visitor_id: Mapped[str | None] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(String(400))
    original_name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(60))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    visitor_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)  # NULL = shared sample data
    direction: Mapped[str] = mapped_column(String(10), default="purchase")                 # purchase | sale
    invoice_kind: Mapped[str] = mapped_column(String(20), default="unknown")
    status: Mapped[str] = mapped_column(String(20), default="processing", index=True)
    # processing | needs_review | confirmed | failed | rejected
    invoice_no: Mapped[str | None] = mapped_column(String(60))
    seller_name: Mapped[str | None] = mapped_column(String(200))
    seller_economic_code: Mapped[str | None] = mapped_column(String(20))
    seller_national_id: Mapped[str | None] = mapped_column(String(20))
    seller_registration_no: Mapped[str | None] = mapped_column(String(30))
    seller_address: Mapped[str | None] = mapped_column(String(400))
    seller_phone: Mapped[str | None] = mapped_column(String(30))
    buyer_name: Mapped[str | None] = mapped_column(String(200))
    buyer_economic_code: Mapped[str | None] = mapped_column(String(20))
    buyer_national_code: Mapped[str | None] = mapped_column(String(20))
    buyer_address: Mapped[str | None] = mapped_column(String(400))
    date_jalali: Mapped[str | None] = mapped_column(String(10))
    jalali_year: Mapped[int | None] = mapped_column(Integer)
    jalali_month: Mapped[int | None] = mapped_column(Integer)
    date_gregorian: Mapped[date | None] = mapped_column(Date)
    unique_tax_id: Mapped[str | None] = mapped_column(String(40))
    # money: integers in the invoice's own `currency` (IRR rial / IRT toman); never mixed without conversion
    subtotal: Mapped[int | None] = mapped_column(BigInteger)
    total_discount: Mapped[int | None] = mapped_column(BigInteger)
    vat: Mapped[int | None] = mapped_column(BigInteger)
    other_levies: Mapped[int | None] = mapped_column(BigInteger)
    payable: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    payment_method: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    source_file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id"))
    field_confidence: Mapped[dict | None] = mapped_column(JSON)
    quality_notes: Mapped[list | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)

    items: Mapped[list["InvoiceItem"]] = relationship(cascade="all, delete-orphan", order_by="InvoiceItem.row_no")
    issues: Mapped[list["ValidationIssue"]] = relationship(cascade="all, delete-orphan")
    category: Mapped[Category | None] = relationship()
    source_file: Mapped[SourceFile | None] = relationship()
    runs: Mapped[list["ExtractionRun"]] = relationship(cascade="all, delete-orphan")

    __table_args__ = (Index("ix_inv_stats", "visitor_id", "status", "jalali_year", "jalali_month"),
                      Index("ix_inv_dup", "seller_name", "invoice_no"))


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    row_no: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(String(300))
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(30))
    unit_price: Mapped[int | None] = mapped_column(BigInteger)
    discount: Mapped[int | None] = mapped_column(BigInteger)
    line_total: Mapped[int | None] = mapped_column(BigInteger)
    line_tax: Mapped[int | None] = mapped_column(BigInteger)


class ValidationIssue(Base):
    __tablename__ = "validation_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(60))
    code: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))   # error | warning | info
    message_fa: Mapped[str] = mapped_column(String(300))


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(80))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    raw_json: Mapped[dict | None] = mapped_column(JSON)   # sensitive identifiers are masked before storing
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelUsage(Base):
    """Per-day counter backing the global cost cap."""
    __tablename__ = "model_usage"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
