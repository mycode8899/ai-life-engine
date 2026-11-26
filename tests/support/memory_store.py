# SPDX-License-Identifier: Apache-2.0
"""In-memory StateStore used to prove the runtime is not tied to SQLite."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from ai_life_engine.agents.models import (
    TOOL_CALL_RETENTION,
    ToolCallRecord,
    ToolCallStatus,
    ToolDecision,
)
from ai_life_engine.brain.models import (
    MODEL_USAGE_RETENTION,
    UsageRecord,
    UsageStatus,
    UsageTotals,
)
from ai_life_engine.events.models import EVENT_RETENTION, EngineEvent, EventStatus
from ai_life_engine.exceptions import ConfigurationError, PersistenceError
from ai_life_engine.goals.models import (
    PLAN_HISTORY,
    REFLECTION_RETENTION,
    Goal,
    GoalStatus,
    Plan,
    PlanStatus,
    Reflection,
    ReflectionOutcome,
    Step,
    StepStatus,
)
from ai_life_engine.heartbeat.models import (
    HEARTBEAT_TICK_RETENTION,
    HeartbeatSnapshot,
    HeartbeatTick,
    NewHeartbeatTick,
)
from ai_life_engine.memory.models import EPISODE_RETENTION, Episode, Fact, WorkingMemory
from ai_life_engine.persistence.models import (
    AuditEntry,
    Checkpoint,
    CommittedState,
    EngineRecord,
    NewAudit,
    NewCheckpoint,
)
from ai_life_engine.validation import bounded_limit


class MemoryStateStore:
    """Small reference store. Not a supported production backend."""

    def __init__(self) -> None:
        self._record: EngineRecord | None = None
        self._audits: list[AuditEntry] = []
        self._checkpoints: list[Checkpoint] = []
        self._heartbeat: HeartbeatSnapshot | None = None
        self._ticks: list[HeartbeatTick] = []
        self._events: list[EngineEvent] = []
        self._working: list[WorkingMemory] = []
        self._episodes: list[Episode] = []
        self._facts: list[Fact] = []
        self._usage: list[UsageRecord] = []
        self._goals: list[Goal] = []
        self._plans: list[Plan] = []
        self._steps: list[Step] = []
        self._reflections: list[Reflection] = []
        self._tool_calls: list[ToolCallRecord] = []
        self._closed = False
        self._next_id = 1

    def load(self, engine_id: UUID | None) -> EngineRecord | None:
        self._require_open()
        if self._record is None:
            return None
        if engine_id is not None and self._record.engine_id != engine_id:
            raise ConfigurationError(
                "The data directory already belongs to a different engine.",
                code="engine_mismatch",
            )
        return self._record

    def commit_state(
        self,
        record: EngineRecord,
        *,
        expected_generation: int | None,
        audit: NewAudit,
        checkpoint: NewCheckpoint,
    ) -> CommittedState:
        self._require_open()
        if audit.engine_id != record.engine_id or checkpoint.engine_id != record.engine_id:
            raise PersistenceError("Audit and checkpoint must use the runtime id.", code="invalid")
        if checkpoint.state is not record.state:
            raise PersistenceError(
                "Checkpoint state must match the runtime record.",
                code="invalid",
            )
        if expected_generation is None:
            if self._record is not None:
                raise PersistenceError(
                    "This data directory already contains a runtime.",
                    code="conflict",
                )
            stored = record.model_copy(update={"generation": 1})
        else:
            if self._record is None or self._record.generation != expected_generation:
                raise PersistenceError(
                    "The runtime state was updated by someone else.",
                    code="conflict",
                )
            if self._record.engine_id != record.engine_id:
                raise PersistenceError(
                    "The runtime state was updated by someone else.",
                    code="conflict",
                )
            stored = record.model_copy(update={"generation": expected_generation + 1})
        audit_entry = AuditEntry(
            id=self._allocate(),
            engine_id=audit.engine_id,
            run_id=audit.run_id,
            action=audit.action,
            from_state=audit.from_state,
            to_state=audit.to_state,
            detail=audit.detail,
            created_at=audit.created_at,
        )
        checkpoint_entry = Checkpoint(
            id=self._allocate(),
            engine_id=checkpoint.engine_id,
            run_id=checkpoint.run_id,
            state=checkpoint.state,
            reason=checkpoint.reason,
            created_at=checkpoint.created_at,
            detail=dict(checkpoint.detail),
        )
        self._record = stored
        self._audits.append(audit_entry)
        self._checkpoints.append(checkpoint_entry)
        return CommittedState(record=stored, audit=audit_entry, checkpoint=checkpoint_entry)

    def list_audit(self, engine_id: UUID, *, limit: int) -> list[AuditEntry]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [entry for entry in self._audits if entry.engine_id == engine_id]
        return list(reversed(rows[-bounded:]))

    def list_checkpoints(self, engine_id: UUID, *, limit: int) -> list[Checkpoint]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [entry for entry in self._checkpoints if entry.engine_id == engine_id]
        return list(reversed(rows[-bounded:]))

    def load_heartbeat(self, engine_id: UUID) -> HeartbeatSnapshot | None:
        self._require_open()
        if self._heartbeat is None:
            return None
        if self._heartbeat.engine_id != engine_id:
            raise ConfigurationError(
                "The data directory already belongs to a different engine.",
                code="engine_mismatch",
            )
        return self._heartbeat

    def commit_heartbeat(
        self,
        record: HeartbeatSnapshot,
        *,
        expected_generation: int | None,
        tick: NewHeartbeatTick | None = None,
    ) -> HeartbeatSnapshot:
        self._require_open()
        if self._record is None or self._record.engine_id != record.engine_id:
            raise PersistenceError(
                "Heartbeat state requires the runtime identity.",
                code="missing",
            )
        if tick is not None and tick.engine_id != record.engine_id:
            raise PersistenceError("Heartbeat tick must use the runtime id.", code="invalid")
        if expected_generation is None:
            if self._heartbeat is not None:
                raise PersistenceError(
                    "Heartbeat state was updated by someone else.",
                    code="conflict",
                )
            stored = record.model_copy(update={"generation": 1})
        else:
            if (
                self._heartbeat is None
                or self._heartbeat.generation != expected_generation
                or self._heartbeat.engine_id != record.engine_id
            ):
                raise PersistenceError(
                    "Heartbeat state was updated by someone else.",
                    code="conflict",
                )
            stored = record.model_copy(update={"generation": expected_generation + 1})
        if tick is not None:
            self._ticks.append(
                HeartbeatTick(
                    id=self._allocate(),
                    engine_id=tick.engine_id,
                    tick_number=tick.tick_number,
                    wake_reason=tick.wake_reason,
                    reasoning_required=tick.reasoning_required,
                    reasoning_executed=tick.reasoning_executed,
                    task_count=tick.task_count,
                    runtime_state=tick.runtime_state,
                    detail=tick.detail,
                    created_at=tick.created_at,
                )
            )
            mine = [item for item in self._ticks if item.engine_id == record.engine_id]
            overflow = mine[:-HEARTBEAT_TICK_RETENTION]
            if overflow:
                drop = {item.id for item in overflow}
                self._ticks = [item for item in self._ticks if item.id not in drop]
        self._heartbeat = stored
        return stored

    def list_heartbeat_ticks(self, engine_id: UUID, *, limit: int) -> list[HeartbeatTick]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [item for item in self._ticks if item.engine_id == engine_id]
        return list(reversed(rows[-bounded:]))

    def append_event(
        self,
        *,
        event_id: UUID,
        engine_id: UUID,
        event_type: str,
        source: str,
        payload: dict[str, str],
        created_at: datetime,
    ) -> EngineEvent:
        self._require_open()
        if self._record is None or self._record.engine_id != engine_id:
            raise PersistenceError("An event requires the runtime identity.", code="missing")
        event = EngineEvent(
            id=self._allocate(),
            event_id=event_id,
            engine_id=engine_id,
            type=event_type,
            source=source,
            payload=dict(payload),
            status=EventStatus.PENDING,
            created_at=created_at,
        )
        self._events.append(event)
        self._trim_events(engine_id)
        return event

    def list_events(self, engine_id: UUID, *, limit: int) -> list[EngineEvent]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [item for item in self._events if item.engine_id == engine_id]
        return list(reversed(rows[-bounded:]))

    def list_pending_events(self, engine_id: UUID, *, limit: int) -> list[EngineEvent]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [
            item
            for item in self._events
            if item.engine_id == engine_id and item.status is EventStatus.PENDING
        ]
        return rows[:bounded]

    def count_pending_events(self, engine_id: UUID) -> int:
        self._require_open()
        return sum(
            1
            for item in self._events
            if item.engine_id == engine_id and item.status is EventStatus.PENDING
        )

    def mark_event(
        self,
        engine_id: UUID,
        event_id: UUID,
        *,
        status: EventStatus,
        handler_error: str | None,
        delivered_at: datetime,
    ) -> None:
        self._require_open()
        if status is EventStatus.PENDING:
            raise PersistenceError("An event cannot be marked pending again.", code="invalid")
        updated: list[EngineEvent] = []
        for item in self._events:
            if (
                item.engine_id == engine_id
                and item.event_id == event_id
                and item.status is EventStatus.PENDING
            ):
                updated.append(
                    item.model_copy(
                        update={
                            "status": status,
                            "handler_error": handler_error,
                            "delivered_at": delivered_at,
                        }
                    )
                )
            else:
                updated.append(item)
        self._events = updated

    def latest_event(self, engine_id: UUID) -> EngineEvent | None:
        self._require_open()
        rows = [item for item in self._events if item.engine_id == engine_id]
        if not rows:
            return None
        return rows[-1]

    def _trim_events(self, engine_id: UUID) -> None:
        finished = [
            item
            for item in self._events
            if item.engine_id == engine_id and item.status is not EventStatus.PENDING
        ]
        overflow = finished[:-EVENT_RETENTION] if len(finished) > EVENT_RETENTION else []
        if not overflow:
            return
        drop = {item.id for item in overflow}
        self._events = [item for item in self._events if item.id not in drop]

    def upsert_working(
        self,
        *,
        engine_id: UUID,
        key: str,
        value: str,
        payload: dict[str, str],
        updated_at: datetime,
    ) -> WorkingMemory:
        self._require_identity(engine_id)
        for item in self._working:
            if item.engine_id == engine_id and item.key == key:
                stored = item.model_copy(
                    update={"value": value, "payload": dict(payload), "updated_at": updated_at}
                )
                self._working = [
                    stored if current.id == item.id else current for current in self._working
                ]
                return stored
        stored = WorkingMemory(
            id=self._allocate(),
            engine_id=engine_id,
            key=key,
            value=value,
            payload=dict(payload),
            updated_at=updated_at,
        )
        self._working.append(stored)
        return stored

    def get_working(self, engine_id: UUID, key: str) -> WorkingMemory | None:
        self._require_open()
        for item in self._working:
            if item.engine_id == engine_id and item.key == key:
                return item
        return None

    def list_working(self, engine_id: UUID) -> list[WorkingMemory]:
        self._require_open()
        rows = [item for item in self._working if item.engine_id == engine_id]
        return sorted(rows, key=lambda item: (item.updated_at, item.id), reverse=True)

    def delete_working(self, engine_id: UUID, key: str) -> bool:
        self._require_open()
        kept: list[WorkingMemory] = []
        for item in self._working:
            if item.engine_id == engine_id and item.key == key:
                continue
            kept.append(item)
        deleted = len(kept) != len(self._working)
        self._working = kept
        return deleted

    def clear_working(self, engine_id: UUID) -> None:
        self._require_open()
        self._working = [item for item in self._working if item.engine_id != engine_id]

    def count_working(self, engine_id: UUID) -> int:
        self._require_open()
        return sum(1 for item in self._working if item.engine_id == engine_id)

    def append_episode(
        self,
        *,
        episode_id: UUID,
        engine_id: UUID,
        summary: str,
        source: str,
        payload: dict[str, str],
        created_at: datetime,
    ) -> Episode:
        self._require_identity(engine_id)
        stored = Episode(
            id=self._allocate(),
            episode_id=episode_id,
            engine_id=engine_id,
            summary=summary,
            source=source,
            payload=dict(payload),
            created_at=created_at,
        )
        self._episodes.append(stored)
        rows = [item for item in self._episodes if item.engine_id == engine_id]
        overflow = rows[:-EPISODE_RETENTION] if len(rows) > EPISODE_RETENTION else []
        if overflow:
            drop = {item.id for item in overflow}
            self._episodes = [item for item in self._episodes if item.id not in drop]
        return stored

    def list_episodes(self, engine_id: UUID, *, limit: int) -> list[Episode]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [item for item in self._episodes if item.engine_id == engine_id]
        return list(reversed(rows[-bounded:]))

    def count_episodes(self, engine_id: UUID) -> int:
        self._require_open()
        return sum(1 for item in self._episodes if item.engine_id == engine_id)

    def upsert_fact(
        self,
        *,
        fact_id: UUID,
        engine_id: UUID,
        key: str,
        content: str,
        payload: dict[str, str],
        created_at: datetime,
        updated_at: datetime,
    ) -> Fact:
        self._require_identity(engine_id)
        for item in self._facts:
            if item.engine_id == engine_id and item.key == key:
                stored = item.model_copy(
                    update={
                        "content": content,
                        "payload": dict(payload),
                        "updated_at": updated_at,
                    }
                )
                self._facts = [
                    stored if current.id == item.id else current for current in self._facts
                ]
                return stored
        stored = Fact(
            id=self._allocate(),
            fact_id=fact_id,
            engine_id=engine_id,
            key=key,
            content=content,
            payload=dict(payload),
            created_at=created_at,
            updated_at=updated_at,
        )
        self._facts.append(stored)
        return stored

    def get_fact(self, engine_id: UUID, key: str) -> Fact | None:
        self._require_open()
        for item in self._facts:
            if item.engine_id == engine_id and item.key == key:
                return item
        return None

    def list_facts(self, engine_id: UUID, *, limit: int) -> list[Fact]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [item for item in self._facts if item.engine_id == engine_id]
        ordered = sorted(rows, key=lambda item: (item.updated_at, item.id), reverse=True)
        return ordered[:bounded]

    def delete_fact(self, engine_id: UUID, key: str) -> bool:
        self._require_open()
        kept: list[Fact] = []
        for item in self._facts:
            if item.engine_id == engine_id and item.key == key:
                continue
            kept.append(item)
        deleted = len(kept) != len(self._facts)
        self._facts = kept
        return deleted

    def count_facts(self, engine_id: UUID) -> int:
        self._require_open()
        return sum(1 for item in self._facts if item.engine_id == engine_id)

    def append_usage(
        self,
        *,
        request_id: UUID,
        engine_id: UUID,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        status: UsageStatus,
        error: str | None,
        created_at: datetime,
    ) -> UsageRecord:
        self._require_identity(engine_id)
        stored = UsageRecord(
            id=self._allocate(),
            request_id=request_id,
            engine_id=engine_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            status=status,
            error=error,
            created_at=created_at,
        )
        self._usage.append(stored)
        rows = [item for item in self._usage if item.engine_id == engine_id]
        overflow = rows[:-MODEL_USAGE_RETENTION] if len(rows) > MODEL_USAGE_RETENTION else []
        if overflow:
            drop = {item.id for item in overflow}
            self._usage = [item for item in self._usage if item.id not in drop]
        return stored

    def list_usage(self, engine_id: UUID, *, limit: int) -> list[UsageRecord]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [item for item in self._usage if item.engine_id == engine_id]
        return list(reversed(rows[-bounded:]))

    def latest_usage(self, engine_id: UUID) -> UsageRecord | None:
        rows = self.list_usage(engine_id, limit=1)
        if not rows:
            return None
        return rows[0]

    def usage_totals(self, engine_id: UUID) -> UsageTotals:
        self._require_open()
        rows = [item for item in self._usage if item.engine_id == engine_id]
        return UsageTotals(
            requests=len(rows),
            errors=sum(1 for item in rows if item.status is UsageStatus.ERROR),
            prompt_tokens=sum(item.prompt_tokens for item in rows),
            completion_tokens=sum(item.completion_tokens for item in rows),
        )

    def count_goals(self, engine_id: UUID) -> int:
        self._require_open()
        return sum(1 for item in self._goals if item.engine_id == engine_id)

    def add_goal(
        self,
        *,
        goal_id: UUID,
        engine_id: UUID,
        title: str,
        objective: str,
        priority: int,
        status: GoalStatus,
        created_at: datetime,
    ) -> Goal:
        self._require_identity(engine_id)
        stored = Goal(
            id=self._allocate(),
            goal_id=goal_id,
            engine_id=engine_id,
            title=title,
            objective=objective,
            priority=priority,
            status=status,
            created_at=created_at,
            updated_at=created_at,
        )
        self._goals.append(stored)
        return stored

    def get_goal(self, engine_id: UUID, goal_id: UUID) -> Goal | None:
        self._require_open()
        for item in self._goals:
            if item.engine_id == engine_id and item.goal_id == goal_id:
                return item
        return None

    def list_goals(
        self,
        engine_id: UUID,
        *,
        status: GoalStatus | None,
        limit: int,
    ) -> list[Goal]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [
            item
            for item in self._goals
            if item.engine_id == engine_id and (status is None or item.status is status)
        ]
        rows.sort(key=lambda item: (item.priority, item.created_at, item.id))
        return rows[:bounded]

    def update_goal_status(
        self,
        *,
        engine_id: UUID,
        goal_id: UUID,
        status: GoalStatus,
        updated_at: datetime,
    ) -> Goal:
        self._require_identity(engine_id)
        current = self.get_goal(engine_id, goal_id)
        if current is None:
            raise PersistenceError("No goal has that id.", code="missing")
        stored = current.model_copy(update={"status": status, "updated_at": updated_at})
        self._goals = [stored if item.goal_id == goal_id else item for item in self._goals]
        return stored

    def replace_plan(
        self,
        *,
        plan_id: UUID,
        engine_id: UUID,
        goal_id: UUID,
        actions: list[str],
        created_at: datetime,
    ) -> Plan:
        self._require_identity(engine_id)
        if self.get_goal(engine_id, goal_id) is None:
            raise PersistenceError("No goal has that id.", code="missing")
        self._plans = [
            item.model_copy(update={"status": PlanStatus.SUPERSEDED})
            if item.engine_id == engine_id
            and item.goal_id == goal_id
            and item.status is PlanStatus.ACCEPTED
            else item
            for item in self._plans
        ]
        stored = Plan(
            id=self._allocate(),
            plan_id=plan_id,
            engine_id=engine_id,
            goal_id=goal_id,
            status=PlanStatus.ACCEPTED,
            step_count=len(actions),
            created_at=created_at,
        )
        self._plans.append(stored)
        for position, action in enumerate(actions, start=1):
            self._steps.append(
                Step(
                    id=self._allocate(),
                    step_id=uuid4(),
                    engine_id=engine_id,
                    plan_id=plan_id,
                    goal_id=goal_id,
                    position=position,
                    action=action,
                    status=StepStatus.PENDING,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        history = [
            item for item in self._plans if item.engine_id == engine_id and item.goal_id == goal_id
        ]
        overflow = history[:-PLAN_HISTORY] if len(history) > PLAN_HISTORY else []
        if overflow:
            drop = {item.plan_id for item in overflow}
            self._plans = [item for item in self._plans if item.plan_id not in drop]
            self._steps = [item for item in self._steps if item.plan_id not in drop]
        return stored

    def accepted_plan(self, engine_id: UUID, goal_id: UUID) -> Plan | None:
        self._require_open()
        found = [
            item
            for item in self._plans
            if item.engine_id == engine_id
            and item.goal_id == goal_id
            and item.status is PlanStatus.ACCEPTED
        ]
        if not found:
            return None
        return found[-1]

    def list_plans(self, engine_id: UUID, goal_id: UUID, *, limit: int) -> list[Plan]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [
            item for item in self._plans if item.engine_id == engine_id and item.goal_id == goal_id
        ]
        return list(reversed(rows[-bounded:]))

    def list_steps(self, engine_id: UUID, plan_id: UUID) -> list[Step]:
        self._require_open()
        rows = [
            item for item in self._steps if item.engine_id == engine_id and item.plan_id == plan_id
        ]
        return sorted(rows, key=lambda item: item.position)

    def apply_reflection(
        self,
        *,
        reflection_id: UUID,
        engine_id: UUID,
        goal_id: UUID,
        goal_status: GoalStatus,
        step_id: UUID | None,
        step_status: StepStatus | None,
        outcome: ReflectionOutcome,
        note: str,
        created_at: datetime,
    ) -> Reflection:
        self.update_goal_status(
            engine_id=engine_id,
            goal_id=goal_id,
            status=goal_status,
            updated_at=created_at,
        )
        if step_id is not None and step_status is not None:
            matched = False
            updated: list[Step] = []
            for item in self._steps:
                same_step = (
                    item.engine_id == engine_id
                    and item.step_id == step_id
                    and item.goal_id == goal_id
                )
                if same_step:
                    updated.append(
                        item.model_copy(update={"status": step_status, "updated_at": created_at})
                    )
                    matched = True
                else:
                    updated.append(item)
            if not matched:
                raise PersistenceError("That step is not on this goal.", code="missing")
            self._steps = updated
        stored = Reflection(
            id=self._allocate(),
            reflection_id=reflection_id,
            engine_id=engine_id,
            goal_id=goal_id,
            step_id=step_id,
            outcome=outcome,
            note=note,
            created_at=created_at,
        )
        self._reflections.append(stored)
        rows = [item for item in self._reflections if item.engine_id == engine_id]
        overflow = rows[:-REFLECTION_RETENTION] if len(rows) > REFLECTION_RETENTION else []
        if overflow:
            drop = {item.id for item in overflow}
            self._reflections = [item for item in self._reflections if item.id not in drop]
        return stored

    def list_reflections(
        self,
        engine_id: UUID,
        goal_id: UUID | None,
        *,
        limit: int,
    ) -> list[Reflection]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [
            item
            for item in self._reflections
            if item.engine_id == engine_id and (goal_id is None or item.goal_id == goal_id)
        ]
        return list(reversed(rows[-bounded:]))

    def append_tool_call(
        self,
        *,
        call_id: UUID,
        engine_id: UUID,
        agent: str,
        tool: str,
        permission: str,
        decision: ToolDecision,
        status: ToolCallStatus,
        detail: str,
        created_at: datetime,
    ) -> ToolCallRecord:
        self._require_identity(engine_id)
        stored = ToolCallRecord(
            id=self._allocate(),
            call_id=call_id,
            engine_id=engine_id,
            agent=agent,
            tool=tool,
            permission=permission,
            decision=decision,
            status=status,
            detail=detail,
            created_at=created_at,
        )
        self._tool_calls.append(stored)
        rows = [item for item in self._tool_calls if item.engine_id == engine_id]
        overflow = rows[:-TOOL_CALL_RETENTION] if len(rows) > TOOL_CALL_RETENTION else []
        if overflow:
            drop = {item.id for item in overflow}
            self._tool_calls = [item for item in self._tool_calls if item.id not in drop]
        return stored

    def list_tool_calls(self, engine_id: UUID, *, limit: int) -> list[ToolCallRecord]:
        self._require_open()
        bounded = bounded_limit(limit)
        rows = [item for item in self._tool_calls if item.engine_id == engine_id]
        return list(reversed(rows[-bounded:]))

    def latest_tool_call(self, engine_id: UUID) -> ToolCallRecord | None:
        rows = self.list_tool_calls(engine_id, limit=1)
        if not rows:
            return None
        return rows[0]

    def _require_identity(self, engine_id: UUID) -> None:
        self._require_open()
        if self._record is None or self._record.engine_id != engine_id:
            raise PersistenceError("A memory record requires the runtime identity.", code="missing")

    def ping(self) -> None:
        self._require_open()

    def close(self) -> None:
        self._closed = True

    def _allocate(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    def _require_open(self) -> None:
        if self._closed:
            raise PersistenceError("Store is closed.", code="closed")
