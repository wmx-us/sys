from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import AuditLog, Expense, ExpenseStatus, Project, ProjectStatus


def csrf_from(response_text: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', response_text)
    assert match, "页面中未找到 CSRF token"
    return match.group(1)


def login(client: TestClient, username: str) -> str:
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={"username": username, "password": "Demo123!", "csrf": token},
    )
    assert response.status_code == 200
    assert response.url.path == "/dashboard"
    return csrf_from(response.text)


def test_role_boundaries(client: TestClient) -> None:
    login(client, "researcher")
    assert client.get("/approvals").status_code == 403
    assert client.get("/finance").status_code == 403

    second_client = TestClient(client.app)
    login(second_client, "finance")
    assert second_client.get("/projects/new").status_code == 403


def test_complete_project_lifecycle(client: TestClient) -> None:
    researcher_csrf = login(client, "researcher")

    response = client.post(
        "/projects/new",
        data={
            "csrf": researcher_csrf,
            "name": "可信科研智能体关键技术研究",
            "summary": "研究科研智能体的可信执行、过程审计与成果评价关键技术。",
            "start_date": "2026-10-10",
            "end_date": "2027-10-09",
            "budget_amount": "200000.00",
        },
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        project = db.scalar(
            select(Project).where(Project.name == "可信科研智能体关键技术研究")
        )
        assert project is not None
        project_id = project.id
        assert project.status == ProjectStatus.DRAFT

    response = client.post(
        f"/projects/{project_id}/submit", data={"csrf": researcher_csrf}
    )
    assert response.status_code == 200

    admin = TestClient(client.app)
    admin_csrf = login(admin, "admin")
    response = admin.post(
        f"/projects/{project_id}/review-initiation",
        data={
            "csrf": admin_csrf,
            "decision": "APPROVED",
            "comment": "研究目标清晰，预算合理，同意立项。",
        },
    )
    assert response.status_code == 200

    response = client.post(
        f"/projects/{project_id}/progress",
        data={
            "csrf": researcher_csrf,
            "completion_percentage": "60",
            "work_summary": "已完成总体架构设计和可信执行原型验证。",
            "risks": "实验数据规模仍需扩大。",
            "next_plan": "完成审计模块并开展对照实验。",
            "report_date": "2027-05-01",
        },
    )
    assert response.status_code == 200

    response = client.post(
        f"/projects/{project_id}/expenses",
        data={
            "csrf": researcher_csrf,
            "category": "材料费",
            "amount": "12000.00",
            "expense_date": "2027-05-02",
            "description": "实验数据采集与标注材料",
        },
    )
    assert response.status_code == 200

    response = client.post(
        f"/projects/{project_id}/achievements",
        data={
            "csrf": researcher_csrf,
            "achievement_type": "SOFTWARE",
            "title": "可信科研智能体原型系统",
            "description": "支持任务执行审计与过程追溯。",
            "reference_url": "https://example.com/demo",
        },
    )
    assert response.status_code == 200

    with SessionLocal() as db:
        expense = db.scalar(select(Expense).where(Expense.project_id == project_id))
        assert expense is not None
        expense_id = expense.id

    finance = TestClient(client.app)
    finance_csrf = login(finance, "finance")
    response = finance.post(
        f"/expenses/{expense_id}/review",
        data={
            "csrf": finance_csrf,
            "decision": "APPROVED",
            "comment": "材料与预算科目相符。",
        },
    )
    assert response.status_code == 200

    response = client.post(
        f"/projects/{project_id}/acceptance",
        data={
            "csrf": researcher_csrf,
            "summary": "项目已完成既定研究目标，形成原型系统和软件成果，经费执行符合预算要求。",
        },
    )
    assert response.status_code == 200

    response = admin.post(
        f"/projects/{project_id}/review-acceptance",
        data={
            "csrf": admin_csrf,
            "decision": "APPROVED",
            "comment": "成果达到任务书要求，同意通过验收。",
        },
    )
    assert response.status_code == 200

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        expense = db.get(Expense, expense_id)
        log_count = db.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.project_id == project_id)
        )
        assert project is not None
        assert project.status == ProjectStatus.COMPLETED
        assert project.code is not None
        assert expense is not None and expense.status == ExpenseStatus.APPROVED
        assert log_count >= 8


def test_expense_cannot_exceed_budget(client: TestClient) -> None:
    researcher_csrf = login(client, "researcher")
    with SessionLocal() as db:
        project = db.scalar(select(Project).where(Project.status == ProjectStatus.ACTIVE))
        assert project is not None
        project_id = project.id

    client.post(
        f"/projects/{project_id}/expenses",
        data={
            "csrf": researcher_csrf,
            "category": "设备费",
            "amount": "400000.00",
            "expense_date": "2026-10-08",
            "description": "高性能计算设备",
        },
    )
    with SessionLocal() as db:
        expense = db.scalar(
            select(Expense)
            .where(Expense.project_id == project_id, Expense.amount == 400000)
            .order_by(Expense.id.desc())
        )
        assert expense is not None
        expense_id = expense.id

    finance = TestClient(client.app)
    finance_csrf = login(finance, "finance")
    response = finance.post(
        f"/expenses/{expense_id}/review",
        data={
            "csrf": finance_csrf,
            "decision": "APPROVED",
            "comment": "尝试审批超预算支出。",
        },
    )
    assert "超过项目预算" in response.text
    with SessionLocal() as db:
        assert db.get(Expense, expense_id).status == ExpenseStatus.PENDING


def test_rejects_non_finite_money_and_unsafe_achievement_url(client: TestClient) -> None:
    researcher_csrf = login(client, "researcher")
    response = client.post(
        "/projects/new",
        data={
            "csrf": researcher_csrf,
            "name": "异常预算项目",
            "summary": "用于验证服务端不会接受非有限金额的项目申报数据。",
            "start_date": "2026-10-10",
            "end_date": "2027-10-09",
            "budget_amount": "NaN",
        },
    )
    assert response.status_code == 400
    assert "项目预算格式无效" in response.text

    with SessionLocal() as db:
        project = db.scalar(select(Project).where(Project.status == ProjectStatus.ACTIVE))
        assert project is not None
        project_id = project.id
    response = client.post(
        f"/projects/{project_id}/achievements",
        data={
            "csrf": researcher_csrf,
            "achievement_type": "SOFTWARE",
            "title": "不安全链接测试",
            "description": "验证链接协议白名单。",
            "reference_url": "javascript:alert(1)",
        },
    )
    assert "成果链接只支持 HTTP 或 HTTPS 地址" in response.text
