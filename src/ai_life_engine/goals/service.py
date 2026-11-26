# SPDX-License-Identifier: Apache-2.0
"""Goal, plan, and reflection API.

``plan`` stores steps the caller supplies. ``propose`` asks the selected
model and then stores those steps. Neither method runs a tool. The default
heartbeat does not call ``propose``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid4

from ai_life_engine.core.time import utcnow
from ai_life_engine.exceptions import (
    ConfigurationError,
    GoalError,
    LifecycleError,
    ModelError,
    PersistenceError,
)
from ai_life_engine.goals.models import (
    GOAL_LIMIT,
    PLAN_STEP_LIMIT,
    Goal,
    GoalStatus,
    GoalSummary,
    NextAction,
    Plan,
    Reflection,
    ReflectionOutcome,
    Step,
    StepStatus,
)
from ai_life_engine.goals.store import supports_goals
from ai_life_engine.observability import get_logger
from ai_life_engine.validation import bounded_limit, clean_reason

logger = get_logger("ai_life_engine.goals")

_BULLET = re.compile(r"^(?:[-*]|\d+[.)])\s+")
_ADVANCING = {ReflectionOutcome.SUCCEEDED, ReflectionOutcome.SKIPPED}


class GoalHost(Protocol):
    """Narrow view of the runtime the goal API is allowed to touch."""

    @property
    def lock(self) -> object: ...

    @property
    def store(self) -> object: ...

    def engine_id(self) -> UUID: ...

    def ensure_open(self) -> None: ...

    def refresh(self) -> None: ...

    def has_identity(self) -> bool: ...


class Goals:
    """Store intentions, choose a next step, and record what happened."""

    def __init__(self, host: GoalHost, *, complete: Callable[[str], str]) -> None:
        self._host = host
        self._complete = complete

    def add(self, title: str, objective: str, *, priority: int = 3) -> Goal:
        """Create an active goal. This does not build a plan."""

        cleaned_title = _line(title, "Title", 80)
        cleaned_objective = _line(objective, "Objective", 500)
        rank = _priority(priority)
        with self._host.lock:
            store = self._ready()
            engine_id = self._host.engine_id()
            if store.count_goals(engine_id) >= GOAL_LIMIT:
                raise GoalError(f"This runtime already has {GOAL_LIMIT} goals.", code="full")
            stored = store.add_goal(
                goal_id=uuid4(),
                engine_id=engine_id,
                title=cleaned_title,
                objective=cleaned_objective,
                priority=rank,
                status=GoalStatus.ACTIVE,
                created_at=utcnow(),
            )
        logger.info("goal added id=%s priority=%s", stored.goal_id, stored.priority)
        return stored

    def get(self, goal_id: UUID) -> Goal:
        """Return one goal or raise when the id is unknown."""

        parsed = _uuid(goal_id, "goal_id")
        with self._host.lock:
            store = self._ready()
            stored = store.get_goal(self._host.engine_id(), parsed)
        if stored is None:
            raise GoalError("No goal has that id.", code="not_found")
        return stored

    def list_goals(
        self,
        *,
        status: GoalStatus | str | None = None,
        limit: int = 20,
    ) -> list[Goal]:
        """Return goals, most urgent first."""

        parsed = None if status is None else _status(status)
        bounded = _limit(limit)
        with self._host.lock:
            store = self._ready()
            return store.list_goals(self._host.engine_id(), status=parsed, limit=bounded)

    def set_status(self, goal_id: UUID, status: GoalStatus | str) -> Goal:
        """Change a goal's status without rewriting its plan."""

        parsed_id = _uuid(goal_id, "goal_id")
        parsed = _status(status)
        with self._host.lock:
            store = self._ready()
            goal = self._require_goal(store, parsed_id)
            if goal.status is parsed:
                return goal
            stored = store.update_goal_status(
                engine_id=self._host.engine_id(),
                goal_id=parsed_id,
                status=parsed,
                updated_at=utcnow(),
            )
        logger.info("goal status id=%s status=%s", stored.goal_id, stored.status.value)
        return stored

    def plan(self, goal_id: UUID, steps: list[str]) -> Plan:
        """Replace the accepted plan with these steps. No model is called."""

        parsed_id = _uuid(goal_id, "goal_id")
        actions = _actions(steps)
        with self._host.lock:
            store = self._ready()
            goal = self._require_goal(store, parsed_id)
            _require_open_goal(goal)
            stored = store.replace_plan(
                plan_id=uuid4(),
                engine_id=self._host.engine_id(),
                goal_id=parsed_id,
                actions=actions,
                created_at=utcnow(),
            )
        logger.info("plan accepted goal=%s steps=%s", parsed_id, stored.step_count)
        return stored

    def propose(self, goal_id: UUID) -> Plan:
        """Ask the selected model for steps, then store that plan.

        This is explicit. A heartbeat tick does not call it.
        """

        parsed_id = _uuid(goal_id, "goal_id")
        with self._host.lock:
            store = self._ready()
            goal = self._require_goal(store, parsed_id)
            _require_open_goal(goal)
            title = goal.title
            objective = goal.objective
        prompt = (
            "List up to 8 concrete steps for this goal.\n"
            "One step per line. No numbering.\n"
            f"Title: {title}\n"
            f"Objective: {objective}"
        )
        text = self._complete(prompt)
        return self.plan(parsed_id, _parse_steps(text))

    def plans(self, goal_id: UUID, *, limit: int = 5) -> list[Plan]:
        """Return plans for one goal, newest first."""

        parsed_id = _uuid(goal_id, "goal_id")
        bounded = _limit(limit)
        with self._host.lock:
            store = self._ready()
            self._require_goal(store, parsed_id)
            return store.list_plans(self._host.engine_id(), parsed_id, limit=bounded)

    def steps(self, plan_id: UUID) -> list[Step]:
        """Return the steps of one plan, in position order."""

        parsed_id = _uuid(plan_id, "plan_id")
        with self._host.lock:
            store = self._ready()
            rows = store.list_steps(self._host.engine_id(), parsed_id)
        if not rows:
            raise GoalError("No plan has that id.", code="not_found")
        return rows

    def next_action(self) -> NextAction | None:
        """Return the next pending step, or None when nothing is eligible.

        Active goals are considered by priority, then by age. Blocked, done,
        and dropped goals are skipped. The step text is not executed.
        """

        with self._host.lock:
            store = self._ready()
            return self._next_unlocked(store)

    def reflect(
        self,
        goal_id: UUID,
        *,
        outcome: ReflectionOutcome | str,
        note: str,
        step_id: UUID | None = None,
    ) -> Reflection:
        """Record an outcome and, for success or skip, advance that step."""

        parsed_id = _uuid(goal_id, "goal_id")
        parsed_step = None if step_id is None else _uuid(step_id, "step_id")
        parsed_outcome = _outcome(outcome)
        cleaned = _line(note, "Note", 500)
        with self._host.lock:
            store = self._ready()
            goal = self._require_goal(store, parsed_id)
            if goal.status is GoalStatus.DROPPED:
                raise GoalError("A dropped goal cannot be reflected on.", code="conflict")
            if goal.status is GoalStatus.DONE and parsed_outcome is not ReflectionOutcome.NOTED:
                raise GoalError(
                    "A finished goal only accepts a noted reflection.",
                    code="conflict",
                )
            step, goal_status, step_status = self._reflection_change(
                store,
                goal,
                parsed_outcome,
                parsed_step,
            )
            stored = store.apply_reflection(
                reflection_id=uuid4(),
                engine_id=self._host.engine_id(),
                goal_id=parsed_id,
                goal_status=goal_status,
                step_id=None if step is None else step.step_id,
                step_status=step_status,
                outcome=parsed_outcome,
                note=cleaned,
                created_at=utcnow(),
            )
        logger.info(
            "reflection stored goal=%s outcome=%s",
            parsed_id,
            stored.outcome.value,
        )
        return stored

    def reflections(
        self,
        goal_id: UUID | None = None,
        *,
        limit: int = 20,
    ) -> list[Reflection]:
        """Return reflections, newest first. Pass a goal id to filter."""

        parsed = None if goal_id is None else _uuid(goal_id, "goal_id")
        bounded = _limit(limit)
        with self._host.lock:
            store = self._ready()
            if parsed is not None:
                self._require_goal(store, parsed)
            return store.list_reflections(self._host.engine_id(), parsed, limit=bounded)

    def summary_for_health(self) -> GoalSummary | None:
        """Return goal counts, or None when storage cannot answer."""

        try:
            self._host.ensure_open()
            if not supports_goals(self._host.store):
                return None
            with self._host.lock:
                if not self._host.has_identity():
                    return None
                return self._summary_unlocked(self._host.store)
        except PersistenceError:
            return None

    def task_view(self) -> tuple[int, str | None]:
        """Return the pending-step count and the next action text."""

        with self._host.lock:
            store = self._ready()
            summary = self._summary_unlocked(store)
            nxt = self._next_unlocked(store)
        label = None if nxt is None else nxt.action
        return summary.pending_steps, label

    def _ready(self) -> object:
        self._host.ensure_open()
        self._host.refresh()
        if not self._host.has_identity():
            raise LifecycleError(
                "Initialize or start the runtime before using goals.",
                code="not_running",
            )
        store = self._host.store
        if not supports_goals(store):
            raise ConfigurationError("This state store does not persist goals.")
        return store

    def _require_goal(self, store: object, goal_id: UUID) -> Goal:
        stored = store.get_goal(self._host.engine_id(), goal_id)
        if stored is None:
            raise GoalError("No goal has that id.", code="not_found")
        return stored

    def _next_unlocked(self, store: object) -> NextAction | None:
        goals = store.list_goals(self._host.engine_id(), status=GoalStatus.ACTIVE, limit=GOAL_LIMIT)
        for goal in goals:
            plan = store.accepted_plan(self._host.engine_id(), goal.goal_id)
            if plan is None:
                continue
            for step in store.list_steps(self._host.engine_id(), plan.plan_id):
                if step.status is not StepStatus.PENDING:
                    continue
                return NextAction(
                    goal_id=goal.goal_id,
                    goal_title=goal.title,
                    priority=goal.priority,
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    position=step.position,
                    action=step.action,
                )
        return None

    def _summary_unlocked(self, store: object) -> GoalSummary:
        engine_id = self._host.engine_id()
        goals = store.list_goals(engine_id, status=None, limit=GOAL_LIMIT)
        active = 0
        blocked = 0
        unplanned = 0
        pending = 0
        for goal in goals:
            if goal.status is GoalStatus.ACTIVE:
                active += 1
            elif goal.status is GoalStatus.BLOCKED:
                blocked += 1
            if goal.status is not GoalStatus.ACTIVE:
                continue
            plan = store.accepted_plan(engine_id, goal.goal_id)
            if plan is None:
                unplanned += 1
                continue
            pending += sum(
                1
                for step in store.list_steps(engine_id, plan.plan_id)
                if step.status is StepStatus.PENDING
            )
        return GoalSummary(
            goals=len(goals),
            active=active,
            blocked=blocked,
            unplanned=unplanned,
            pending_steps=pending,
        )

    def _reflection_change(
        self,
        store: object,
        goal: Goal,
        outcome: ReflectionOutcome,
        step_id: UUID | None,
    ) -> tuple[Step | None, GoalStatus, StepStatus | None]:
        plan = store.accepted_plan(self._host.engine_id(), goal.goal_id)
        steps = [] if plan is None else store.list_steps(self._host.engine_id(), plan.plan_id)
        if outcome in _ADVANCING:
            if plan is None:
                raise GoalError("This goal has no accepted plan.", code="conflict")
            step = _pick_step(steps, step_id, pending_only=True)
            if outcome is ReflectionOutcome.SUCCEEDED:
                status = StepStatus.DONE
            else:
                status = StepStatus.SKIPPED
            remaining = [
                item
                for item in steps
                if item.step_id != step.step_id and item.status is StepStatus.PENDING
            ]
            goal_status = GoalStatus.DONE if not remaining else goal.status
            return step, goal_status, status
        step = None
        if step_id is not None:
            if plan is None:
                raise GoalError("This goal has no accepted plan.", code="conflict")
            step = _pick_step(steps, step_id, pending_only=False)
        goal_status = GoalStatus.BLOCKED if outcome is ReflectionOutcome.BLOCKED else goal.status
        return step, goal_status, None


def _require_open_goal(goal: Goal) -> None:
    if goal.status in {GoalStatus.DONE, GoalStatus.DROPPED}:
        raise GoalError("This goal is not open for a new plan.", code="conflict")


def _pick_step(steps: list[Step], step_id: UUID | None, *, pending_only: bool) -> Step:
    if step_id is None:
        for step in steps:
            if step.status is StepStatus.PENDING:
                return step
        raise GoalError("This plan has no pending step.", code="conflict")
    for step in steps:
        if step.step_id != step_id:
            continue
        if pending_only and step.status is not StepStatus.PENDING:
            raise GoalError("That step is not pending.", code="conflict")
        return step
    raise GoalError("That step is not on the accepted plan.", code="not_found")


def _actions(steps: object) -> list[str]:
    if not isinstance(steps, list) or not steps or len(steps) > PLAN_STEP_LIMIT:
        raise GoalError(
            f"A plan needs 1..{PLAN_STEP_LIMIT} steps.",
            code="invalid_argument",
        )
    return [_line(item, "Step", 200) for item in steps]


def _parse_steps(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        stripped = _BULLET.sub("", line.strip())
        if not stripped:
            continue
        try:
            found.append(clean_reason(stripped, limit=200))
        except ValueError:
            continue
        if len(found) == PLAN_STEP_LIMIT:
            break
    if not found:
        raise ModelError("The model did not return any plan steps.", code="rejected")
    return found


def _line(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise GoalError(f"{label} must be text.", code="invalid_argument")
    try:
        return clean_reason(value, limit=limit)
    except ValueError as exc:
        raise GoalError(f"{label} must be 1..{limit} characters.", code="invalid_argument") from exc


def _priority(value: object) -> int:
    if type(value) is not int or value < 1 or value > 5:
        raise GoalError("priority must be an integer from 1 to 5.", code="invalid_argument")
    return value


def _status(value: object) -> GoalStatus:
    if isinstance(value, GoalStatus):
        return value
    try:
        return GoalStatus(str(value))
    except ValueError as exc:
        raise GoalError("Unknown goal status.", code="invalid_argument") from exc


def _outcome(value: object) -> ReflectionOutcome:
    if isinstance(value, ReflectionOutcome):
        return value
    try:
        return ReflectionOutcome(str(value))
    except ValueError as exc:
        raise GoalError("Unknown reflection outcome.", code="invalid_argument") from exc


def _uuid(value: object, label: str) -> UUID:
    if isinstance(value, UUID):
        return value
    raise GoalError(f"{label} must be a UUID.", code="invalid_argument")


def _limit(limit: int) -> int:
    try:
        return bounded_limit(limit)
    except ValueError as exc:
        raise GoalError(str(exc), code="invalid_argument") from exc
