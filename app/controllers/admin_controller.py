from flask import Blueprint, current_app, redirect, render_template, request, url_for

from app.queries.admin_queries import GetAdminDashboardQuery
from app.repositories.admin_read import AdminReadRepository
from app.security.auth import current_identity

admin_bp = Blueprint("admin", __name__)


def _page_arg() -> int:
    raw = request.args.get("page", "1")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


@admin_bp.get("/dashboard")
def dashboard_page():
    identity = current_identity()
    if identity is None:
        return redirect(url_for("auth.login_page"))
    if not identity.has_any("ADMIN"):
        return render_template("admin/forbidden.html"), 403
    services = current_app.extensions["kindbridge"]
    dashboard = GetAdminDashboardQuery(AdminReadRepository(services.engine)).execute(page=_page_arg())
    return render_template("admin/dashboard.html", dashboard=dashboard)
