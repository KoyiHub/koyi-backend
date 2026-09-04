"""Background work for groups and plans.

Grouping runs after a cohort is placed, not after each child: a group forms
around a weakness several children share, so reconciling on every individual
placement would churn through the same work once per child and form groups from
half a class.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def reconcile_groups_task(self, school_id: str) -> dict:  # noqa: ARG001
    """Bring every active group's membership in line with current placements."""
    from apps.instruction.grouping import GroupingService
    from apps.schools.models import School

    school = School.objects.filter(pk=school_id).first()
    if school is None:
        return {"reconciled": 0}

    changes = GroupingService(school).reconcile_all()
    thin = [str(c.group.pk) for c in changes if c.below_minimum]
    logger.info(
        "groups reconciled",
        extra={
            "school_id": school_id,
            "groups": len(changes),
            "joined": sum(len(c.joined) for c in changes),
            "left": sum(len(c.left) for c in changes),
            "below_minimum": thin,
        },
    )
    return {"reconciled": len(changes), "below_minimum": thin}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def generate_group_plan_task(self, group_id: str) -> dict:  # noqa: ARG001
    """Write the plan for one group.

    Generation can take tens of seconds, so a teacher opening the group polls
    for it rather than waiting on the request.
    """
    from apps.common.services import NotFoundError
    from apps.instruction.models import Group
    from apps.instruction.plans import GroupPlanService

    group = Group.objects.filter(pk=group_id).first()
    if group is None:
        return {"generated": False}

    try:
        plan = GroupPlanService(group).generate()
    except NotFoundError as exc:
        # No members, or nothing they share. Not an error worth retrying.
        logger.info("no plan generated", extra={"group_id": group_id, "reason": str(exc)})
        return {"generated": False, "reason": str(exc)}

    return {"generated": True, "plan_id": str(plan.pk), "status": plan.status}
