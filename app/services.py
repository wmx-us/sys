"""科管系统核心业务服务。

路由层只负责 HTTP 输入输出；本模块集中执行角色权限、资源归属、状态机和
跨实体一致性规则，确保从页面或其他接口调用时得到相同的业务结果。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AcceptanceSubmission,
    Achievement,
    AchievementType,
    Approval,
    ApprovalType,
    AuditLog,
    Decision,
    Expense,
    ExpenseStatus,
    ProgressReport,
    Project,
    ProjectStatus,
    Role,
    User,
)


class BusinessError(ValueError):
    """可安全展示给用户的业务规则错误。"""

    pass


class PermissionDenied(BusinessError):
    """角色或资源归属不满足时抛出的业务异常。"""

    pass


def require_role(user: User, *roles: Role) -> None:
    """要求用户属于任一允许角色；不能依赖前端隐藏按钮实现授权。"""

    if user.role not in roles:
        raise PermissionDenied("你没有执行此操作的权限")


def require_owner(user: User, project: Project) -> None:
    """要求当前用户是项目负责人，并且具有科研人员角色。"""

    if user.role != Role.RESEARCHER or project.owner_id != user.id:
        raise PermissionDenied("只有项目负责人可以执行此操作")


def can_view_project(user: User, project: Project) -> bool:
    """按角色和项目阶段计算读取权限。

    财务人员不应看到尚未立项的申报材料，只能访问进入执行阶段后的项目。
    """

    if user.role == Role.ADMIN:
        return True
    if user.role == Role.RESEARCHER:
        return project.owner_id == user.id
    return project.status in {
        ProjectStatus.ACTIVE,
        ProjectStatus.PENDING_ACCEPTANCE,
        ProjectStatus.ACCEPTANCE_REJECTED,
        ProjectStatus.COMPLETED,
    }


def audit(
    db: Session,
    actor: User,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    project: Project | None = None,
    detail: dict | None = None,
) -> None:
    """在当前业务事务中追加审计记录，由调用方统一提交。"""

    db.add(
        AuditLog(
            actor_id=actor.id,
            project_id=project.id if project else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail_json=detail or {},
        )
    )


def create_project(
    db: Session,
    actor: User,
    *,
    name: str,
    summary: str,
    start_date: date,
    end_date: date,
    budget_amount: Decimal,
) -> Project:
    """创建归属于科研人员的项目草稿。"""

    require_role(actor, Role.RESEARCHER)
    _validate_project(name, summary, start_date, end_date, budget_amount)
    project = Project(
        name=name.strip(),
        summary=summary.strip(),
        owner_id=actor.id,
        start_date=start_date,
        end_date=end_date,
        budget_amount=budget_amount,
    )
    db.add(project)
    db.flush()
    audit(db, actor, "PROJECT_CREATED", "Project", project.id, project)
    db.commit()
    return project


def update_project(
    db: Session,
    actor: User,
    project: Project,
    *,
    name: str,
    summary: str,
    start_date: date,
    end_date: date,
    budget_amount: Decimal,
) -> Project:
    """更新尚未进入审批或已被驳回的申报信息。"""

    require_owner(actor, project)
    if project.status not in {ProjectStatus.DRAFT, ProjectStatus.REJECTED}:
        raise BusinessError("当前状态不允许修改申报信息")
    _validate_project(name, summary, start_date, end_date, budget_amount)
    project.name = name.strip()
    project.summary = summary.strip()
    project.start_date = start_date
    project.end_date = end_date
    project.budget_amount = budget_amount
    project.version += 1
    audit(db, actor, "PROJECT_UPDATED", "Project", project.id, project)
    db.commit()
    return project


def submit_project(db: Session, actor: User, project: Project) -> None:
    """将草稿/驳回项目送入立项审批，并锁定申报编辑入口。"""

    require_owner(actor, project)
    if project.status not in {ProjectStatus.DRAFT, ProjectStatus.REJECTED}:
        raise BusinessError("只有草稿或已驳回项目可以提交")
    project.status = ProjectStatus.PENDING_APPROVAL
    project.rejection_reason = None
    project.version += 1
    audit(db, actor, "PROJECT_SUBMITTED", "Project", project.id, project)
    db.commit()


def review_initiation(
    db: Session, actor: User, project: Project, decision: Decision, comment: str
) -> None:
    """执行立项审批，并把审批记录、状态变化和审计日志原子提交。"""

    require_role(actor, Role.ADMIN)
    if project.status != ProjectStatus.PENDING_APPROVAL:
        raise BusinessError("该项目当前不在立项审批中")
    if not comment.strip():
        raise BusinessError("请填写审批意见")
    db.add(
        Approval(
            project_id=project.id,
            type=ApprovalType.PROJECT_INITIATION,
            decision=decision,
            comment=comment.strip(),
            reviewer_id=actor.id,
        )
    )
    if decision == Decision.APPROVED:
        project.status = ProjectStatus.ACTIVE
        project.rejection_reason = None
        db.flush()
        # 使用数据库主键生成稳定且可读的项目编号，避免额外维护序列表。
        project.code = f"KY-{date.today().year}-{project.id:04d}"
        action = "PROJECT_APPROVED"
    else:
        project.status = ProjectStatus.REJECTED
        project.rejection_reason = comment.strip()
        action = "PROJECT_REJECTED"
    project.version += 1
    audit(db, actor, action, "Project", project.id, project, {"comment": comment.strip()})
    db.commit()


def add_progress(
    db: Session,
    actor: User,
    project: Project,
    *,
    completion_percentage: int,
    work_summary: str,
    risks: str,
    next_plan: str,
    report_date: date,
) -> ProgressReport:
    """新增项目进展，并保证累计完成比例不会倒退。"""

    require_owner(actor, project)
    _require_active(project)
    if not 0 <= completion_percentage <= 100:
        raise BusinessError("完成比例必须在 0 到 100 之间")
    latest = db.scalar(
        select(func.max(ProgressReport.completion_percentage)).where(
            ProgressReport.project_id == project.id
        )
    )
    if latest is not None and completion_percentage < latest:
        raise BusinessError(f"完成比例不能低于上一记录的 {latest}%")
    if not work_summary.strip():
        raise BusinessError("请填写本期工作总结")
    report = ProgressReport(
        project_id=project.id,
        reporter_id=actor.id,
        completion_percentage=completion_percentage,
        work_summary=work_summary.strip(),
        risks=risks.strip(),
        next_plan=next_plan.strip(),
        report_date=report_date,
    )
    db.add(report)
    db.flush()
    audit(db, actor, "PROGRESS_ADDED", "ProgressReport", report.id, project)
    db.commit()
    return report


def add_expense(
    db: Session,
    actor: User,
    project: Project,
    *,
    category: str,
    amount: Decimal,
    expense_date: date,
    description: str,
) -> Expense:
    """登记待财务审核的支出；此时尚不计入已执行预算。"""

    require_owner(actor, project)
    _require_active(project)
    if not amount.is_finite() or amount <= 0:
        raise BusinessError("支出金额必须大于 0")
    if not category.strip() or not description.strip():
        raise BusinessError("支出类别和说明不能为空")
    expense = Expense(
        project_id=project.id,
        applicant_id=actor.id,
        category=category.strip(),
        amount=amount,
        expense_date=expense_date,
        description=description.strip(),
    )
    db.add(expense)
    db.flush()
    audit(db, actor, "EXPENSE_SUBMITTED", "Expense", expense.id, project)
    db.commit()
    return expense


def review_expense(
    db: Session, actor: User, expense: Expense, decision: Decision, comment: str
) -> None:
    """审核支出，在最终批准时重新计算预算余额。

    不信任申请提交时的余额快照，因为在等待审核期间其他支出可能已经获批。
    正式高并发数据库还应在此处增加项目行锁或原子条件更新。
    """

    require_role(actor, Role.FINANCE)
    if expense.status != ExpenseStatus.PENDING:
        raise BusinessError("该支出已经处理")
    if not comment.strip():
        raise BusinessError("请填写审核意见")
    project = expense.project
    if decision == Decision.APPROVED:
        # 预算只统计 APPROVED 记录；待审核和已驳回支出不占用已执行额度。
        approved_total = db.scalar(
            select(func.coalesce(func.sum(Expense.amount), 0)).where(
                Expense.project_id == project.id,
                Expense.status == ExpenseStatus.APPROVED,
            )
        )
        if Decimal(approved_total) + expense.amount > project.budget_amount:
            raise BusinessError("审核后累计支出将超过项目预算")
        expense.status = ExpenseStatus.APPROVED
        action = "EXPENSE_APPROVED"
    else:
        expense.status = ExpenseStatus.REJECTED
        action = "EXPENSE_REJECTED"
    expense.reviewer_id = actor.id
    expense.review_comment = comment.strip()
    from app.models import utcnow

    expense.reviewed_at = utcnow()
    audit(db, actor, action, "Expense", expense.id, project, {"comment": comment.strip()})
    db.commit()


def add_achievement(
    db: Session,
    actor: User,
    project: Project,
    *,
    achievement_type: AchievementType,
    title: str,
    description: str,
    reference_url: str,
) -> Achievement:
    """登记成果，并在服务端限制外部链接协议。"""

    require_owner(actor, project)
    _require_active(project)
    if not title.strip():
        raise BusinessError("成果名称不能为空")
    normalized_url = reference_url.strip()
    if normalized_url and urlparse(normalized_url).scheme not in {"http", "https"}:
        raise BusinessError("成果链接只支持 HTTP 或 HTTPS 地址")
    achievement = Achievement(
        project_id=project.id,
        type=achievement_type,
        title=title.strip(),
        description=description.strip(),
        reference_url=normalized_url or None,
        created_by=actor.id,
    )
    db.add(achievement)
    db.flush()
    audit(db, actor, "ACHIEVEMENT_ADDED", "Achievement", achievement.id, project)
    db.commit()
    return achievement


def submit_acceptance(db: Session, actor: User, project: Project, summary: str) -> None:
    """提交验收并保存版本化快照。

    至少一条进展保证存在执行过程；清空待审核支出保证结项后预算不会继续变化。
    """

    require_owner(actor, project)
    if project.status not in {ProjectStatus.ACTIVE, ProjectStatus.ACCEPTANCE_REJECTED}:
        raise BusinessError("当前状态不允许提交验收")
    if len(summary.strip()) < 20:
        raise BusinessError("验收总结至少填写 20 个字符")
    progress_count = db.scalar(
        select(func.count(ProgressReport.id)).where(ProgressReport.project_id == project.id)
    )
    if not progress_count:
        raise BusinessError("提交验收前至少需要一条进展记录")
    pending_expense_count = db.scalar(
        select(func.count(Expense.id)).where(
            Expense.project_id == project.id, Expense.status == ExpenseStatus.PENDING
        )
    )
    if pending_expense_count:
        raise BusinessError("仍有待审核支出，请处理后再提交验收")
    # 驳回后重新提交时新增版本，而不是覆盖上一轮验收材料。
    current_version = db.scalar(
        select(func.coalesce(func.max(AcceptanceSubmission.version), 0)).where(
            AcceptanceSubmission.project_id == project.id
        )
    )
    submission = AcceptanceSubmission(
        project_id=project.id,
        summary=summary.strip(),
        submitted_by=actor.id,
        version=int(current_version) + 1,
    )
    db.add(submission)
    project.status = ProjectStatus.PENDING_ACCEPTANCE
    project.rejection_reason = None
    project.version += 1
    db.flush()
    audit(db, actor, "ACCEPTANCE_SUBMITTED", "AcceptanceSubmission", submission.id, project)
    db.commit()


def review_acceptance(
    db: Session, actor: User, project: Project, decision: Decision, comment: str
) -> None:
    """执行验收决定；通过后进入不可继续写业务数据的终态。"""

    require_role(actor, Role.ADMIN)
    if project.status != ProjectStatus.PENDING_ACCEPTANCE:
        raise BusinessError("该项目当前不在验收审批中")
    if not comment.strip():
        raise BusinessError("请填写验收意见")
    db.add(
        Approval(
            project_id=project.id,
            type=ApprovalType.PROJECT_ACCEPTANCE,
            decision=decision,
            comment=comment.strip(),
            reviewer_id=actor.id,
        )
    )
    if decision == Decision.APPROVED:
        project.status = ProjectStatus.COMPLETED
        project.rejection_reason = None
        action = "ACCEPTANCE_APPROVED"
    else:
        project.status = ProjectStatus.ACCEPTANCE_REJECTED
        project.rejection_reason = comment.strip()
        action = "ACCEPTANCE_REJECTED"
    project.version += 1
    audit(db, actor, action, "Project", project.id, project, {"comment": comment.strip()})
    db.commit()


def approved_expense_total(db: Session, project_id: int) -> Decimal:
    """计算真实预算执行额，只汇总财务已批准的支出。"""

    value = db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.project_id == project_id, Expense.status == ExpenseStatus.APPROVED
        )
    )
    return Decimal(value)


def _require_active(project: Project) -> None:
    """统一保护进度、支出和成果的执行期写操作。"""

    if project.status != ProjectStatus.ACTIVE:
        raise BusinessError("只有执行中的项目可以执行此操作")


def _validate_project(
    name: str, summary: str, start_date: date, end_date: date, budget_amount: Decimal
) -> None:
    """校验项目基本字段，并拒绝 NaN/Infinity 等非有限金额。"""

    if len(name.strip()) < 2:
        raise BusinessError("项目名称至少需要 2 个字符")
    if len(summary.strip()) < 10:
        raise BusinessError("项目摘要至少需要 10 个字符")
    if end_date < start_date:
        raise BusinessError("结束日期不能早于开始日期")
    if not budget_amount.is_finite() or budget_amount <= 0:
        raise BusinessError("项目预算必须大于 0")
