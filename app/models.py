"""科研项目领域模型。

这里仅定义数据结构和关系；权限、状态转换和跨实体约束统一放在服务层，避免
ORM 实体、路由和模板各自维护一套业务规则。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    """返回带时区的 UTC 时间，数据库时间统一以 UTC 保存。"""

    return datetime.now(timezone.utc)


class Role(str, Enum):
    """最小职责集合：项目发起、科研治理和资金治理。"""

    RESEARCHER = "RESEARCHER"
    ADMIN = "ADMIN"
    FINANCE = "FINANCE"


class ProjectStatus(str, Enum):
    """项目生命周期状态；合法转换由 ``app.services`` 集中控制。"""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    REJECTED = "REJECTED"
    ACTIVE = "ACTIVE"
    PENDING_ACCEPTANCE = "PENDING_ACCEPTANCE"
    ACCEPTANCE_REJECTED = "ACCEPTANCE_REJECTED"
    COMPLETED = "COMPLETED"


class ApprovalType(str, Enum):
    PROJECT_INITIATION = "PROJECT_INITIATION"
    PROJECT_ACCEPTANCE = "PROJECT_ACCEPTANCE"


class Decision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExpenseStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AchievementType(str, Enum):
    PAPER = "PAPER"
    PATENT = "PATENT"
    SOFTWARE = "SOFTWARE"
    REPORT = "REPORT"
    OTHER = "OTHER"


class User(Base):
    """系统用户；MVP 中一个用户对应一个固定业务角色。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[Role] = mapped_column(SqlEnum(Role, native_enum=False), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    projects: Mapped[list[Project]] = relationship(back_populates="owner")


class Project(Base):
    """生命周期聚合根，进度、支出、成果和验收记录均归属于项目。"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    summary: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    budget_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[ProjectStatus] = mapped_column(
        SqlEnum(ProjectStatus, native_enum=False), default=ProjectStatus.DRAFT, index=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 每次修改或状态转换递增，为后续引入乐观并发控制保留基础。
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    progress_reports: Mapped[list[ProgressReport]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    expenses: Mapped[list[Expense]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    achievements: Mapped[list[Achievement]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    acceptance_submissions: Mapped[list[AcceptanceSubmission]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Approval(Base):
    """不可覆盖的立项/验收审批历史，每次决定保存为一条新记录。"""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    type: Mapped[ApprovalType] = mapped_column(SqlEnum(ApprovalType, native_enum=False))
    decision: Mapped[Decision] = mapped_column(SqlEnum(Decision, native_enum=False))
    comment: Mapped[str] = mapped_column(Text)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="approvals")
    reviewer: Mapped[User] = relationship()


class ProgressReport(Base):
    """项目阶段进展快照；服务层保证完成比例不能倒退。"""

    __tablename__ = "progress_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    completion_percentage: Mapped[int] = mapped_column(Integer)
    work_summary: Mapped[str] = mapped_column(Text)
    risks: Mapped[str] = mapped_column(Text, default="")
    next_plan: Mapped[str] = mapped_column(Text, default="")
    report_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="progress_reports")
    reporter: Mapped[User] = relationship()


class Expense(Base):
    """项目支出申请；只有财务批准后的金额才计入预算执行额。"""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    expense_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[ExpenseStatus] = mapped_column(
        SqlEnum(ExpenseStatus, native_enum=False), default=ExpenseStatus.PENDING, index=True
    )
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="expenses")
    applicant: Mapped[User] = relationship(foreign_keys=[applicant_id])
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewer_id])


class Achievement(Base):
    """论文、专利、软件等项目成果的统一记录。"""

    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    type: Mapped[AchievementType] = mapped_column(SqlEnum(AchievementType, native_enum=False))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    reference_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="achievements")
    creator: Mapped[User] = relationship()


class AcceptanceSubmission(Base):
    """验收提交快照，通过版本号保留驳回后再次提交的历史。"""

    __tablename__ = "acceptance_submissions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_acceptance_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    summary: Mapped[str] = mapped_column(Text)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer)

    project: Mapped[Project] = relationship(back_populates="acceptance_submissions")
    submitter: Mapped[User] = relationship()


class AuditLog(Base):
    """只追加的关键操作日志，供项目生命周期追溯。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    actor: Mapped[User] = relationship()
    project: Mapped[Project | None] = relationship()
