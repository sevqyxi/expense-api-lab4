from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

ROLE_USER = "user"
ROLE_AUDITOR = "auditor"
ROLE_ADMIN = "admin"
ROLES = (ROLE_USER, ROLE_AUDITOR, ROLE_ADMIN)


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'auditor', 'admin')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # у старих користувачів з ЛР3 username і password_hash порожні — увійти вони не можуть
    username: Mapped[Optional[str]] = mapped_column(String(50), unique=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_USER, server_default=ROLE_USER)

    # при видаленні користувача видаляються його персональні категорії та записи
    categories: Mapped[List["CategoryModel"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True
    )
    records: Mapped[List["RecordModel"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class CategoryModel(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # NULL — глобальна категорія, інакше — персональна категорія користувача
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    owner: Mapped[Optional["UserModel"]] = relationship(back_populates="categories")
    records: Mapped[List["RecordModel"]] = relationship(
        back_populates="category", cascade="all, delete-orphan", passive_deletes=True
    )


class RecordModel(Base):
    __tablename__ = "records"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_records_amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # гроші зберігаються як Numeric(12, 2), а не float
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    user: Mapped["UserModel"] = relationship(back_populates="records")
    category: Mapped["CategoryModel"] = relationship(back_populates="records")
