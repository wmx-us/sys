"""幂等的本地演示数据初始化。

预置不同状态的项目和三类账号，让评审者无需先录入大量数据即可检查各模块。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.database import SessionLocal, create_schema
from app.models import (
    Achievement,
    AchievementType,
    Expense,
    ExpenseStatus,
    ProgressReport,
    Project,
    ProjectStatus,
    Role,
    User,
)
from app.security import hash_password


DEMO_PASSWORD = "Demo123!"


def ensure_demo_data() -> None:
    """仅在用户表为空时写入完整演示数据，重复执行不会产生副本。"""

    create_schema()
    with SessionLocal() as db:
        user_count = db.scalar(select(func.count(User.id)))
        if user_count:
            return
        users = {
            Role.RESEARCHER: User(
                username="researcher",
                display_name="张研究员",
                password_hash=hash_password(DEMO_PASSWORD),
                role=Role.RESEARCHER,
            ),
            Role.ADMIN: User(
                username="admin",
                display_name="李管理员",
                password_hash=hash_password(DEMO_PASSWORD),
                role=Role.ADMIN,
            ),
            Role.FINANCE: User(
                username="finance",
                display_name="王财务",
                password_hash=hash_password(DEMO_PASSWORD),
                role=Role.FINANCE,
            ),
        }
        db.add_all(users.values())
        db.flush()
        today = date.today()
        draft = Project(
            name="多模态科研数据治理方法研究",
            summary="研究科研数据的统一分类、质量评估与多模态治理方法。",
            owner_id=users[Role.RESEARCHER].id,
            start_date=today,
            end_date=today + timedelta(days=365),
            budget_amount=Decimal("300000.00"),
            status=ProjectStatus.DRAFT,
        )
        pending = Project(
            name="实验设备智能运维平台",
            summary="利用传感数据实现科研实验设备的故障预测和智能维护。",
            owner_id=users[Role.RESEARCHER].id,
            start_date=today,
            end_date=today + timedelta(days=540),
            budget_amount=Decimal("500000.00"),
            status=ProjectStatus.PENDING_APPROVAL,
        )
        active = Project(
            code=f"KY-{today.year}-0003",
            name="科研知识图谱关键技术研究",
            summary="构建覆盖项目、人员、成果和机构关系的科研知识图谱。",
            owner_id=users[Role.RESEARCHER].id,
            start_date=today - timedelta(days=120),
            end_date=today + timedelta(days=245),
            budget_amount=Decimal("420000.00"),
            status=ProjectStatus.ACTIVE,
        )
        db.add_all([draft, pending, active])
        db.flush()
        db.add_all(
            [
                ProgressReport(
                    project_id=active.id,
                    reporter_id=users[Role.RESEARCHER].id,
                    completion_percentage=35,
                    work_summary="完成领域本体设计和首批科研数据清洗。",
                    risks="部分历史数据字段标准不一致。",
                    next_plan="完成实体对齐和关系抽取验证。",
                    report_date=today - timedelta(days=7),
                ),
                Expense(
                    project_id=active.id,
                    applicant_id=users[Role.RESEARCHER].id,
                    category="设备费",
                    amount=Decimal("68000.00"),
                    expense_date=today - timedelta(days=30),
                    description="图计算实验服务器采购",
                    status=ExpenseStatus.APPROVED,
                    reviewer_id=users[Role.FINANCE].id,
                    review_comment="预算科目与材料齐全",
                ),
                Expense(
                    project_id=active.id,
                    applicant_id=users[Role.RESEARCHER].id,
                    category="会议费",
                    amount=Decimal("8500.00"),
                    expense_date=today,
                    description="阶段成果研讨会",
                    status=ExpenseStatus.PENDING,
                ),
                Achievement(
                    project_id=active.id,
                    type=AchievementType.PAPER,
                    title="科研知识图谱中的实体对齐方法",
                    description="已完成论文初稿。",
                    created_by=users[Role.RESEARCHER].id,
                ),
            ]
        )
        db.commit()


if __name__ == "__main__":
    ensure_demo_data()
    print("演示数据已准备完成。")
