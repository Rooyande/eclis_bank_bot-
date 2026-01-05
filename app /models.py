from sqlalchemy import (
    String, Integer, BigInteger, Text, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


# Account Types: USER | BANK | COURT | JOB
# Roles: ROOT | BANK_ADMIN | JOB_ADMIN
# Ledger tx_type: transfer | salary | upgrade | levelup | admin_adjust
# Ledger status: ok | failed


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)          # USER/BANK/COURT/JOB
    owner_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    balance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_accounts_owner", "owner_tg_id"),
        Index("idx_accounts_type", "type"),
    )


class Role(Base):
    __tablename__ = "roles"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)        # ROOT/BANK_ADMIN/JOB_ADMIN
    scope: Mapped[str | None] = mapped_column(String(64), primary_key=True, nullable=True)  # job_account_id or null
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_roles_tg", "tg_id"),
        Index("idx_roles_role", "role"),
    )


class RegisterCode(Base):
    __tablename__ = "register_codes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)        # active/used/revoked/expired
    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_codes_status", "status"),
        Index("idx_codes_created_by", "created_by"),
    )


class Ledger(Base):
    __tablename__ = "ledger"

    txid: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    from_acc: Mapped[str] = mapped_column(String(64), ForeignKey("accounts.id"), nullable=False)
    to_acc: Mapped[str] = mapped_column(String(64), ForeignKey("accounts.id"), nullable=False)

    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    reason: Mapped[str] = mapped_column(String(256), default="")
    meta: Mapped[str] = mapped_column(Text, default="{}")                  # json string
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_ledger_from", "from_acc"),
        Index("idx_ledger_to", "to_acc"),
        Index("idx_ledger_actor", "actor_tg_id"),
        Index("idx_ledger_type", "tx_type"),
    )


# IDs for system accounts
BANK_ACC_ID = "ACC-BANK"
COURT_ACC_ID = "ACC-COURT"

