from app.server.context import AppContext

from ..schemas import RepairProjectMembersInput, RepairProjectMembersResult


async def repair_project_members(
    request: RepairProjectMembersInput,
    context: AppContext,
) -> RepairProjectMembersResult:
    # doctor: public — this is an operator-only entrypoint, not an HTTP route.
    scanned, repaired = await context.projects.repair_invalid_member_ids(
        dry_run=request.dry_run
    )
    return RepairProjectMembersResult(
        scanned=scanned,
        repaired=repaired,
        dry_run=request.dry_run,
    )
