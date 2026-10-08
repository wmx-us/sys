"""服务端页面路由与 HTTP 适配层。

本模块负责解析表单、建立页面上下文和处理 PRG（Post/Redirect/Get）；所有
关键权限、状态转换和一致性规则仍由 ``app.services`` 负责。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import get_db
from app.models import (
    AchievementType,
    AuditLog,
    Decision,
    Expense,
    ExpenseStatus,
    Project,
    ProjectStatus,
    Role,
    User,
)
from app.security import (
    create_session_token,
    csrf_token,
    read_session_token,
    validate_csrf,
    verify_password,
)
from app.services import (
    BusinessError,
    add_achievement,
    add_expense,
    add_progress,
    approved_expense_total,
    can_view_project,
    create_project,
    review_acceptance,
    review_expense,
    review_initiation,
    submit_acceptance,
    submit_project,
    update_project,
)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ROLE_LABELS = {
    Role.RESEARCHER: "科研人员",
    Role.ADMIN: "科研管理员",
    Role.FINANCE: "财务人员",
}

STATUS_LABELS = {
    ProjectStatus.DRAFT: "草稿",
    ProjectStatus.PENDING_APPROVAL: "待立项审批",
    ProjectStatus.REJECTED: "立项驳回",
    ProjectStatus.ACTIVE: "执行中",
    ProjectStatus.PENDING_ACCEPTANCE: "待验收",
    ProjectStatus.ACCEPTANCE_REJECTED: "验收驳回",
    ProjectStatus.COMPLETED: "已结项",
}

EXPENSE_STATUS_LABELS = {
    ExpenseStatus.PENDING: "待审核",
    ExpenseStatus.APPROVED: "已通过",
    ExpenseStatus.REJECTED: "已驳回",
}

ACHIEVEMENT_LABELS = {
    AchievementType.PAPER: "论文",
    AchievementType.PATENT: "专利",
    AchievementType.SOFTWARE: "软件",
    AchievementType.REPORT: "报告",
    AchievementType.OTHER: "其他",
}


DbDep = Annotated[Session, Depends(get_db)]


def _current_user(request: Request, db: Session) -> User | None:
    """从签名 Cookie 恢复有效用户；失效或停用账号按未登录处理。"""

    user_id = read_session_token(request.cookies.get("session"))
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    return user


def _require_user(request: Request, db: Session) -> User:
    """要求登录，并用 303 跳转保证普通浏览器回到登录页。"""

    user = _current_user(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"}
        )
    return user


def _verify_csrf_or_403(token: str, user: User | None = None) -> None:
    """统一校验所有写表单的 CSRF token。"""

    if not validate_csrf(token, user.id if user else None):
        raise HTTPException(status_code=403, detail="表单已过期，请刷新页面后重试")


def _context(
    request: Request,
    user: User | None = None,
    *,
    title: str = "",
    **kwargs: object,
) -> dict[str, object]:
    """生成所有模板共享的用户、标签、提示消息和 CSRF 上下文。"""

    return {
        "request": request,
        "app_name": settings.app_name,
        "title": title,
        "current_user": user,
        "csrf_token": csrf_token(user.id if user else None),
        "role_labels": ROLE_LABELS,
        "status_labels": STATUS_LABELS,
        "expense_status_labels": EXPENSE_STATUS_LABELS,
        "achievement_labels": ACHIEVEMENT_LABELS,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
        **kwargs,
    }


def _redirect(path: str, *, success: str | None = None, error: str | None = None) -> RedirectResponse:
    """执行 PRG 跳转，并把一次性结果消息放入查询参数。"""

    message = success or error
    if message:
        key = "success" if success else "error"
        base, fragment = (path.split("#", 1) + [""])[:2] if "#" in path else (path, "")
        separator = "&" if "?" in base else "?"
        path = f"{base}{separator}{key}={quote(message)}"
        if fragment:
            path = f"{path}#{fragment}"
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def _project_or_404(db: Session, project_id: int) -> Project:
    """一次性预加载详情页需要的项目聚合数据，避免模板内产生 N+1 查询。"""

    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.owner),
            selectinload(Project.approvals),
            selectinload(Project.progress_reports),
            selectinload(Project.expenses),
            selectinload(Project.achievements),
            selectinload(Project.acceptance_submissions),
        )
    )
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _parse_date(raw: str, field_name: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise BusinessError(f"{field_name}格式无效") from exc


def _parse_money(raw: str, field_name: str = "金额") -> Decimal:
    """将表单金额规范为两位小数，并拒绝 NaN/Infinity。"""

    try:
        value = Decimal(raw)
        if not value.is_finite():
            raise InvalidOperation
        return value.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise BusinessError(f"{field_name}格式无效") from exc


# ---------------------------------------------------------------------------
# 基础页面与认证
# ---------------------------------------------------------------------------


@router.get("/", include_in_schema=False)
def root(request: Request, db: DbDep) -> RedirectResponse:
    return _redirect("/dashboard" if _current_user(request, db) else "/login")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: DbDep) -> Response:
    if _current_user(request, db):
        return _redirect("/dashboard")
    return templates.TemplateResponse(
        "login.html", _context(request, title="登录", demo_password="Demo123!")
    )


@router.post("/login")
def login(
    request: Request,
    db: DbDep,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    csrf: Annotated[str, Form()],
) -> Response:
    _verify_csrf_or_403(csrf)
    user = db.scalar(select(User).where(User.username == username.strip()))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            _context(request, title="登录", error="用户名或密码错误", demo_password="Demo123!"),
            status_code=400,
        )
    response = _redirect("/dashboard", success=f"欢迎回来，{user.display_name}")
    response.set_cookie(
        "session",
        create_session_token(user.id),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return response


@router.post("/logout")
def logout(
    request: Request, db: DbDep, csrf: Annotated[str, Form()]
) -> RedirectResponse:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    response = _redirect("/login", success="你已安全退出")
    response.delete_cookie("session")
    return response


# ---------------------------------------------------------------------------
# 角色化工作台与项目查询
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: DbDep) -> HTMLResponse:
    user = _require_user(request, db)
    project_query = select(Project)
    if user.role == Role.RESEARCHER:
        project_query = project_query.where(Project.owner_id == user.id)
    elif user.role == Role.FINANCE:
        project_query = project_query.where(
            Project.status.in_(
                [
                    ProjectStatus.ACTIVE,
                    ProjectStatus.PENDING_ACCEPTANCE,
                    ProjectStatus.ACCEPTANCE_REJECTED,
                    ProjectStatus.COMPLETED,
                ]
            )
        )
    projects = list(db.scalars(project_query.order_by(Project.updated_at.desc())).all())
    status_counts: dict[ProjectStatus, int] = {}
    for project in projects:
        status_counts[project.status] = status_counts.get(project.status, 0) + 1
    if user.role == Role.ADMIN:
        todo_count = sum(
            1
            for project in projects
            if project.status
            in {ProjectStatus.PENDING_APPROVAL, ProjectStatus.PENDING_ACCEPTANCE}
        )
    elif user.role == Role.FINANCE:
        todo_count = db.scalar(
            select(func.count(Expense.id)).where(Expense.status == ExpenseStatus.PENDING)
        )
    else:
        todo_count = sum(
            1
            for project in projects
            if project.status in {ProjectStatus.REJECTED, ProjectStatus.ACCEPTANCE_REJECTED}
        )
    recent_logs = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.project_id.in_([p.id for p in projects])
                if projects
                else AuditLog.id == -1
            )
            .options(selectinload(AuditLog.actor), selectinload(AuditLog.project))
            .order_by(AuditLog.created_at.desc())
            .limit(8)
        ).all()
    )
    return templates.TemplateResponse(
        "dashboard.html",
        _context(
            request,
            user,
            title="工作台",
            projects=projects[:6],
            project_count=len(projects),
            todo_count=todo_count or 0,
            status_counts=status_counts,
            active_count=status_counts.get(ProjectStatus.ACTIVE, 0),
            completed_count=status_counts.get(ProjectStatus.COMPLETED, 0),
            recent_logs=recent_logs,
        ),
    )


@router.get("/projects", response_class=HTMLResponse)
def project_list(
    request: Request,
    db: DbDep,
    q: str = "",
    project_status: str = "",
) -> HTMLResponse:
    user = _require_user(request, db)
    query = select(Project).options(selectinload(Project.owner))
    if user.role == Role.RESEARCHER:
        query = query.where(Project.owner_id == user.id)
    elif user.role == Role.FINANCE:
        query = query.where(
            Project.status.in_(
                [
                    ProjectStatus.ACTIVE,
                    ProjectStatus.PENDING_ACCEPTANCE,
                    ProjectStatus.ACCEPTANCE_REJECTED,
                    ProjectStatus.COMPLETED,
                ]
            )
        )
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(or_(Project.name.ilike(pattern), Project.code.ilike(pattern)))
    if project_status:
        try:
            query = query.where(Project.status == ProjectStatus(project_status))
        except ValueError:
            pass
    projects = list(db.scalars(query.order_by(Project.updated_at.desc())).all())
    return templates.TemplateResponse(
        "projects/list.html",
        _context(
            request,
            user,
            title="项目管理",
            projects=projects,
            q=q,
            selected_status=project_status,
            statuses=list(ProjectStatus),
        ),
    )


# ---------------------------------------------------------------------------
# 项目申报与立项状态流转
# ---------------------------------------------------------------------------


@router.get("/projects/new", response_class=HTMLResponse)
def project_new_page(request: Request, db: DbDep) -> HTMLResponse:
    user = _require_user(request, db)
    if user.role != Role.RESEARCHER:
        raise HTTPException(status_code=403, detail="只有科研人员可以创建项目")
    return templates.TemplateResponse(
        "projects/form.html",
        _context(request, user, title="创建项目", project=None, form_action="/projects/new"),
    )


@router.post("/projects/new")
def project_new(
    request: Request,
    db: DbDep,
    csrf: Annotated[str, Form()],
    name: Annotated[str, Form()],
    summary: Annotated[str, Form()],
    start_date: Annotated[str, Form()],
    end_date: Annotated[str, Form()],
    budget_amount: Annotated[str, Form()],
) -> Response:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    try:
        project = create_project(
            db,
            user,
            name=name,
            summary=summary,
            start_date=_parse_date(start_date, "开始日期"),
            end_date=_parse_date(end_date, "结束日期"),
            budget_amount=_parse_money(budget_amount, "项目预算"),
        )
        return _redirect(f"/projects/{project.id}", success="项目草稿已创建")
    except BusinessError as exc:
        return templates.TemplateResponse(
            "projects/form.html",
            _context(
                request,
                user,
                title="创建项目",
                project=None,
                form_action="/projects/new",
                error=str(exc),
                form_data={
                    "name": name,
                    "summary": summary,
                    "start_date": start_date,
                    "end_date": end_date,
                    "budget_amount": budget_amount,
                },
            ),
            status_code=400,
        )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: int, db: DbDep) -> HTMLResponse:
    user = _require_user(request, db)
    project = _project_or_404(db, project_id)
    if not can_view_project(user, project):
        raise HTTPException(status_code=403, detail="你无权查看该项目")
    approved_total = approved_expense_total(db, project.id)
    audit_logs = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.project_id == project.id)
            .options(selectinload(AuditLog.actor))
            .order_by(AuditLog.created_at.desc())
        ).all()
    )
    project.progress_reports.sort(key=lambda item: item.created_at, reverse=True)
    project.expenses.sort(key=lambda item: item.created_at, reverse=True)
    project.achievements.sort(key=lambda item: item.created_at, reverse=True)
    project.approvals.sort(key=lambda item: item.created_at, reverse=True)
    project.acceptance_submissions.sort(key=lambda item: item.version, reverse=True)
    return templates.TemplateResponse(
        "projects/detail.html",
        _context(
            request,
            user,
            title=project.name,
            project=project,
            approved_total=approved_total,
            remaining_budget=project.budget_amount - approved_total,
            audit_logs=audit_logs,
            achievement_types=list(AchievementType),
            today=date.today().isoformat(),
        ),
    )


@router.get("/projects/{project_id}/edit", response_class=HTMLResponse)
def project_edit_page(request: Request, project_id: int, db: DbDep) -> HTMLResponse:
    user = _require_user(request, db)
    project = _project_or_404(db, project_id)
    if project.owner_id != user.id or project.status not in {
        ProjectStatus.DRAFT,
        ProjectStatus.REJECTED,
    }:
        raise HTTPException(status_code=403, detail="当前项目不可编辑")
    return templates.TemplateResponse(
        "projects/form.html",
        _context(
            request,
            user,
            title="编辑项目",
            project=project,
            form_action=f"/projects/{project.id}/edit",
        ),
    )


@router.post("/projects/{project_id}/edit")
def project_edit(
    request: Request,
    project_id: int,
    db: DbDep,
    csrf: Annotated[str, Form()],
    name: Annotated[str, Form()],
    summary: Annotated[str, Form()],
    start_date: Annotated[str, Form()],
    end_date: Annotated[str, Form()],
    budget_amount: Annotated[str, Form()],
) -> Response:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    project = _project_or_404(db, project_id)
    try:
        update_project(
            db,
            user,
            project,
            name=name,
            summary=summary,
            start_date=_parse_date(start_date, "开始日期"),
            end_date=_parse_date(end_date, "结束日期"),
            budget_amount=_parse_money(budget_amount, "项目预算"),
        )
        return _redirect(f"/projects/{project.id}", success="项目信息已更新")
    except BusinessError as exc:
        return templates.TemplateResponse(
            "projects/form.html",
            _context(
                request,
                user,
                title="编辑项目",
                project=project,
                form_action=f"/projects/{project.id}/edit",
                error=str(exc),
                form_data={
                    "name": name,
                    "summary": summary,
                    "start_date": start_date,
                    "end_date": end_date,
                    "budget_amount": budget_amount,
                },
            ),
            status_code=400,
        )


@router.post("/projects/{project_id}/submit")
def project_submit(
    request: Request, project_id: int, db: DbDep, csrf: Annotated[str, Form()]
) -> RedirectResponse:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    project = _project_or_404(db, project_id)
    try:
        submit_project(db, user, project)
        return _redirect(f"/projects/{project.id}", success="项目已提交立项审批")
    except BusinessError as exc:
        return _redirect(f"/projects/{project.id}", error=str(exc))


@router.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request, db: DbDep) -> HTMLResponse:
    user = _require_user(request, db)
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="只有科研管理员可以访问审批中心")
    projects = list(
        db.scalars(
            select(Project)
            .where(
                Project.status.in_(
                    [ProjectStatus.PENDING_APPROVAL, ProjectStatus.PENDING_ACCEPTANCE]
                )
            )
            .options(selectinload(Project.owner))
            .order_by(Project.updated_at)
        ).all()
    )
    return templates.TemplateResponse(
        "approvals.html",
        _context(request, user, title="审批中心", projects=projects),
    )


@router.post("/projects/{project_id}/review-initiation")
def initiation_review(
    request: Request,
    project_id: int,
    db: DbDep,
    csrf: Annotated[str, Form()],
    decision: Annotated[str, Form()],
    comment: Annotated[str, Form()],
) -> RedirectResponse:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    project = _project_or_404(db, project_id)
    try:
        review_initiation(db, user, project, Decision(decision), comment)
        return _redirect(f"/projects/{project.id}", success="立项审批已完成")
    except (BusinessError, ValueError) as exc:
        return _redirect(f"/projects/{project.id}", error=str(exc))


# ---------------------------------------------------------------------------
# 执行期业务：进度、经费和成果
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/progress")
def progress_create(
    request: Request,
    project_id: int,
    db: DbDep,
    csrf: Annotated[str, Form()],
    completion_percentage: Annotated[int, Form()],
    work_summary: Annotated[str, Form()],
    risks: Annotated[str, Form()] = "",
    next_plan: Annotated[str, Form()] = "",
    report_date: Annotated[str, Form()] = "",
) -> RedirectResponse:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    project = _project_or_404(db, project_id)
    try:
        add_progress(
            db,
            user,
            project,
            completion_percentage=completion_percentage,
            work_summary=work_summary,
            risks=risks,
            next_plan=next_plan,
            report_date=_parse_date(report_date, "填报日期"),
        )
        return _redirect(f"/projects/{project.id}#progress", success="进展已保存")
    except BusinessError as exc:
        return _redirect(f"/projects/{project.id}#progress", error=str(exc))


@router.post("/projects/{project_id}/expenses")
def expense_create(
    request: Request,
    project_id: int,
    db: DbDep,
    csrf: Annotated[str, Form()],
    category: Annotated[str, Form()],
    amount: Annotated[str, Form()],
    expense_date: Annotated[str, Form()],
    description: Annotated[str, Form()],
) -> RedirectResponse:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    project = _project_or_404(db, project_id)
    try:
        add_expense(
            db,
            user,
            project,
            category=category,
            amount=_parse_money(amount),
            expense_date=_parse_date(expense_date, "支出日期"),
            description=description,
        )
        return _redirect(f"/projects/{project.id}#expenses", success="支出已提交财务审核")
    except BusinessError as exc:
        return _redirect(f"/projects/{project.id}#expenses", error=str(exc))


@router.get("/finance", response_class=HTMLResponse)
def finance_center(request: Request, db: DbDep) -> HTMLResponse:
    user = _require_user(request, db)
    if user.role != Role.FINANCE:
        raise HTTPException(status_code=403, detail="只有财务人员可以访问经费中心")
    expenses = list(
        db.scalars(
            select(Expense)
            .options(selectinload(Expense.project), selectinload(Expense.applicant))
            .order_by(
                (Expense.status == ExpenseStatus.PENDING).desc(), Expense.created_at.desc()
            )
        ).all()
    )
    return templates.TemplateResponse(
        "finance.html",
        _context(request, user, title="经费中心", expenses=expenses),
    )


@router.post("/expenses/{expense_id}/review")
def expense_review(
    request: Request,
    expense_id: int,
    db: DbDep,
    csrf: Annotated[str, Form()],
    decision: Annotated[str, Form()],
    comment: Annotated[str, Form()],
) -> RedirectResponse:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    expense = db.scalar(
        select(Expense)
        .where(Expense.id == expense_id)
        .options(selectinload(Expense.project))
    )
    if not expense:
        raise HTTPException(status_code=404, detail="支出记录不存在")
    try:
        review_expense(db, user, expense, Decision(decision), comment)
        return _redirect("/finance", success="支出审核已完成")
    except (BusinessError, ValueError) as exc:
        return _redirect("/finance", error=str(exc))


@router.post("/projects/{project_id}/achievements")
def achievement_create(
    request: Request,
    project_id: int,
    db: DbDep,
    csrf: Annotated[str, Form()],
    achievement_type: Annotated[str, Form()],
    title: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    reference_url: Annotated[str, Form()] = "",
) -> RedirectResponse:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    project = _project_or_404(db, project_id)
    try:
        add_achievement(
            db,
            user,
            project,
            achievement_type=AchievementType(achievement_type),
            title=title,
            description=description,
            reference_url=reference_url,
        )
        return _redirect(f"/projects/{project.id}#achievements", success="成果已登记")
    except (BusinessError, ValueError) as exc:
        return _redirect(f"/projects/{project.id}#achievements", error=str(exc))


# ---------------------------------------------------------------------------
# 验收与结项
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/acceptance")
def acceptance_submit(
    request: Request,
    project_id: int,
    db: DbDep,
    csrf: Annotated[str, Form()],
    summary: Annotated[str, Form()],
) -> RedirectResponse:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    project = _project_or_404(db, project_id)
    try:
        submit_acceptance(db, user, project, summary)
        return _redirect(f"/projects/{project.id}", success="项目已提交验收")
    except BusinessError as exc:
        return _redirect(f"/projects/{project.id}#acceptance", error=str(exc))


@router.post("/projects/{project_id}/review-acceptance")
def acceptance_review(
    request: Request,
    project_id: int,
    db: DbDep,
    csrf: Annotated[str, Form()],
    decision: Annotated[str, Form()],
    comment: Annotated[str, Form()],
) -> RedirectResponse:
    user = _require_user(request, db)
    _verify_csrf_or_403(csrf, user)
    project = _project_or_404(db, project_id)
    try:
        review_acceptance(db, user, project, Decision(decision), comment)
        return _redirect(f"/projects/{project.id}", success="验收审批已完成")
    except (BusinessError, ValueError) as exc:
        return _redirect(f"/projects/{project.id}", error=str(exc))
