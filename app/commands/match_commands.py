"""ProposeMatchCommand. The agent writes proposals only through this command.

Flow for one request and one match_attempt:
embed the request, retrieve 15 résumés, hard-filter that set, then score it.
The numeric score is deterministic. The command appends MatchesProposed or
NoMatchFound and does not approve or assign.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import Engine

from app.commands.bus import Command, CommandBus
from app.commands.user_commands import Clock, utc_now
from app.domain.aggregates import HelpRequest
from app.domain.errors import NotFound
from app.domain.events import CONCURRENCY_CLASSIFIED, MATCHES_PROPOSED, NO_MATCH_FOUND
from app.domain.matching import (
    PROPOSAL_LIMIT,
    TOP_N,
    UNKNOWN,
    FilterFacts,
    RequestRecord,
    ScoredVolunteer,
    VolunteerRecord,
    count_rejections,
    default_concurrency,
    rank_proposals,
    rejection_reason,
    render_rationale,
    request_query_text,
    score_candidate,
    skill_overlap,
)
from app.infrastructure.llm import unsafe_travel
from app.infrastructure.vector_store import EmbeddingFailed, ResumeHit, ResumeVectorStore, VectorStoreUnavailable
from app.projections.match_projector import MatchProjector
from app.repositories.event_store import SqlEventStore
from app.repositories.matching import SqlMatchingReader
from mcp_tools.calculate_travel_context import calculate_travel_context
from mcp_tools.check_volunteer_capacity import check_volunteer_capacity

logger = logging.getLogger(__name__)

PIPELINE = (
    "load",
    "retrieve",
    "hard_filter",
    "capacity",
    "travel",
    "web",
    "score_and_write",
)

_RETRIEVAL_ATTEMPTS = 3


def propose_idempotency_key(request_id: UUID, match_attempt: int) -> str:
    return f"propose:{request_id}:{match_attempt}"


@dataclass(frozen=True)
class ProposeMatchCommand(Command):
    request_id: UUID
    match_attempt: int


@dataclass(frozen=True)
class ProposeMatchResult:
    request_id: UUID
    match_attempt: int
    outcome: str
    proposed: int = 0


@dataclass
class _Candidate:
    volunteer: VolunteerRecord
    similarity: float
    feasibility: float = 1.0
    travel_note: str = ""


@dataclass
class MatchWork:
    command: ProposeMatchCommand
    request: HelpRequest | None = None
    view: RequestRecord | None = None
    hits: list[ResumeHit] = field(default_factory=list)
    candidates: list[_Candidate] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    facts: FilterFacts | None = None
    web_lookup: str = "skipped"
    classification: tuple[str, str] | None = None
    stop: bool = False
    outcome: str = ""
    proposed: int = 0
    query_text: str = ""


class WebSearch(Protocol):
    def search(self, city: str, today) -> str: ...


class TravelSafety(Protocol):
    def unsafe(self, evidence: str) -> bool: ...


class RationaleWriter(Protocol):
    def write(self, components: dict[str, float], travel_note: str, web_lookup: str) -> str: ...


class TemplateRationale:
    def write(self, components: dict[str, float], travel_note: str, web_lookup: str) -> str:
        return render_rationale(components, travel_note, web_lookup)


class FallbackRationale:
    """Ask the LLM to phrase the rationale. Keep the template when the call fails.

    The numeric score is already fixed before this runs. Nothing in the model
    reply is parsed back into the score.
    """

    def __init__(self, client, template: TemplateRationale | None = None) -> None:
        self._client = client
        self._template = template or TemplateRationale()

    def write(self, components: dict[str, float], travel_note: str, web_lookup: str) -> str:
        draft = self._template.write(components, travel_note, web_lookup)
        try:
            text = self._client.complete(
                "Write a match rationale of at most 500 characters. Use the draft. "
                f"Keep web_lookup={web_lookup}. Do not change the numeric score.\n{draft}",
                timeout=10,
            )
        except Exception:
            logger.exception("llm_rationale_failed")
            return draft
        cleaned = " ".join(str(text).split())
        if not cleaned or "web_lookup=" not in cleaned:
            return draft
        return cleaned[:500]


class _LlmSafety:
    def __init__(self, client) -> None:
        self._client = client

    def unsafe(self, evidence: str) -> bool:
        return unsafe_travel(self._client, evidence)


class MatchCommandHandlers:
    def __init__(
        self,
        engine: Engine,
        event_store: SqlEventStore,
        reader: SqlMatchingReader,
        projector: MatchProjector,
        resumes: ResumeVectorStore,
        clock: Clock = utc_now,
        web: WebSearch | None = None,
        safety: TravelSafety | None = None,
        rationale: RationaleWriter | None = None,
        sleep: Callable[[float], None] | None = None,
        retrieval_attempts: int = _RETRIEVAL_ATTEMPTS,
    ) -> None:
        self._engine = engine
        self._event_store = event_store
        self._reader = reader
        self._projector = projector
        self._resumes = resumes
        self._clock = clock
        self._web = web
        self._safety = safety
        self._rationale = rationale or TemplateRationale()
        self._sleep = sleep or (lambda _seconds: None)
        self._retrieval_attempts = retrieval_attempts
        self._compiled = None

    def register_on(self, bus: CommandBus) -> None:
        bus.register(ProposeMatchCommand, self.handle)

    def handle(self, command: ProposeMatchCommand) -> ProposeMatchResult:
        work = MatchWork(command=command)
        from agent.graph import invoke_match_graph

        finished = invoke_match_graph(self, work)
        return ProposeMatchResult(
            request_id=command.request_id,
            match_attempt=command.match_attempt,
            outcome=finished.outcome,
            proposed=finished.proposed,
        )

    def load(self, work: MatchWork) -> None:
        if work.stop:
            return
        history = self._event_store.load_stream(work.command.request_id)
        if not history:
            raise NotFound("Help request was not found")
        request = HelpRequest.load(work.command.request_id, history)
        _restore_match_state(request, history)
        if request.already_proposed(work.command.match_attempt):
            work.stop = True
            work.outcome = "noop"
            logger.info(
                "idempotent propose %s",
                propose_idempotency_key(work.command.request_id, work.command.match_attempt),
            )
            return
        work.request = request
        with self._engine.connect() as conn:
            view = self._reader.load_request(conn, work.command.request_id)
        if view is None:
            raise NotFound("Help request was not found")
        concurrency = request.concurrency_type or view.concurrency_type
        if concurrency == UNKNOWN:
            kind, reason = default_concurrency(view.resource_type)
            work.classification = (kind, reason)
            concurrency = kind
        if concurrency != view.concurrency_type:
            view = RequestRecord(
                request_id=view.request_id,
                requester_id=view.requester_id,
                city=view.city,
                category=view.category,
                resource_type=view.resource_type,
                description=view.description,
                urgency=view.urgency,
                slot=view.slot,
                required_skills=view.required_skills,
                requires_vehicle=view.requires_vehicle,
                concurrency_type=concurrency,
            )
        work.view = view

    def retrieve(self, work: MatchWork) -> None:
        if work.stop:
            return
        view = work.view
        assert view is not None
        with self._engine.connect() as conn:
            notes = self._reader.accessibility_notes(conn, view.requester_id)
        work.query_text = request_query_text(view.description, view.category, view.required_skills, notes)
        last_error: Exception | None = None
        for attempt in range(self._retrieval_attempts):
            if attempt:
                self._sleep(0.5 * attempt)
            try:
                work.hits = self._resumes.query_resumes(work.query_text, top_n=TOP_N)
                return
            except EmbeddingFailed as exc:
                last_error = exc
                if attempt == self._retrieval_attempts - 1:
                    logger.exception("embedding_failed")
            except VectorStoreUnavailable as exc:
                last_error = exc
                if attempt == self._retrieval_attempts - 1:
                    logger.exception("vector_store_unavailable")
        work.stop = True
        work.outcome = "deferred"
        logger.error("retrieval_failed %s", last_error)

    def hard_filter(self, work: MatchWork) -> None:
        if work.stop:
            return
        view = work.view
        assert view is not None
        profile_ids = []
        for hit in work.hits:
            try:
                profile_ids.append(UUID(hit.profile_id))
            except ValueError:
                work.rejections.append("inactive")
        with self._engine.connect() as conn:
            volunteers = self._reader.load_volunteers(conn, profile_ids)
            facts = FilterFacts(
                request=view,
                exemptions=self._reader.exemption_user_ids(conn, view.requester_id),
                declined_profile_ids=self._reader.declined_profile_ids(conn, view.request_id),
                periods=self._reader.unavailability(conn, profile_ids),
                assigned=self._reader.assigned_tasks(conn, profile_ids),
                today=self._today(),
            )
        work.facts = facts
        seen: set[UUID] = set()
        for hit in work.hits:
            try:
                profile_id = UUID(hit.profile_id)
            except ValueError:
                continue
            if profile_id in seen:
                continue
            seen.add(profile_id)
            volunteer = volunteers.get(profile_id)
            if volunteer is None:
                work.rejections.append("inactive")
                continue
            reason = rejection_reason(volunteer, facts)
            if reason is not None:
                work.rejections.append(reason)
                continue
            work.candidates.append(_Candidate(volunteer=volunteer, similarity=_unit(hit.similarity)))

    def capacity(self, work: MatchWork) -> None:
        if work.stop:
            return
        view = work.view
        facts = work.facts
        assert view is not None and facts is not None
        kept: list[_Candidate] = []
        for candidate in work.candidates:
            result = check_volunteer_capacity(candidate.volunteer, view, facts.assigned)
            if result["eligible"]:
                kept.append(candidate)
            else:
                work.rejections.append(result["reason"] or "capacity")
        work.candidates = kept

    def travel(self, work: MatchWork) -> None:
        if work.stop:
            return
        view = work.view
        assert view is not None
        for candidate in work.candidates:
            context = calculate_travel_context(
                candidate.volunteer.primary_city,
                view.city,
                view.resource_type,
            )
            candidate.feasibility = float(context["feasibility"])
            candidate.travel_note = str(context["note"])

    def web(self, work: MatchWork) -> None:
        if work.stop or not work.candidates:
            return
        view = work.view
        assert view is not None
        if self._web is None:
            work.web_lookup = "skipped"
            return
        try:
            evidence = self._web.search(view.city, self._today())
        except Exception:
            logger.exception("web_lookup_failed")
            work.web_lookup = "skipped"
            return
        work.web_lookup = "ok"
        unsafe = False
        if self._safety is not None and evidence:
            try:
                unsafe = bool(self._safety.unsafe(evidence))
            except Exception:
                logger.exception("llm_unsafe_travel_failed")
                unsafe = False
        if unsafe:
            for candidate in work.candidates:
                candidate.feasibility *= 0.5
                candidate.travel_note = f"{candidate.travel_note} Travel looks unsafe today.".strip()

    def score_and_write(self, work: MatchWork) -> None:
        if work.stop:
            return
        view = work.view
        request = work.request
        assert view is not None and request is not None
        today = self._today()
        scored: list[ScoredVolunteer] = []
        for candidate in work.candidates:
            volunteer = candidate.volunteer
            score, parts = score_candidate(
                similarity=candidate.similarity,
                skills=skill_overlap(view.required_skills, volunteer.skills),
                travel_feasibility=candidate.feasibility,
                urgency=view.urgency,
                has_vehicle_fit=not view.requires_vehicle or volunteer.has_vehicle,
                frequency=volunteer.base_frequency,
                preferred_date=view.preferred_date,
                today=today,
            )
            try:
                rationale = self._rationale.write(parts, candidate.travel_note, work.web_lookup)
            except Exception:
                logger.exception("llm_rationale_failed")
                rationale = render_rationale(parts, candidate.travel_note, work.web_lookup)
            scored.append(
                ScoredVolunteer(
                    volunteer_id=volunteer.profile_id,
                    score=score,
                    similarity=candidate.similarity,
                    rationale=rationale[:500],
                )
            )
        chosen = rank_proposals(scored)
        payloads: list[tuple[str, dict]] = []
        if work.classification is not None:
            kind, reason = work.classification
            payloads.append(
                (
                    CONCURRENCY_CLASSIFIED,
                    {
                        "concurrency_type": kind,
                        "reason": reason,
                        "match_attempt": work.command.match_attempt,
                    },
                )
            )
        if chosen:
            payloads.append(
                (
                    MATCHES_PROPOSED,
                    {
                        "proposals": [
                            {
                                "assignment_id": str(uuid4()),
                                "volunteer_id": str(item.volunteer_id),
                                "score": item.score,
                                "rationale": item.rationale,
                                "rank": rank,
                            }
                            for rank, item in enumerate(chosen, start=1)
                        ],
                        "match_attempt": work.command.match_attempt,
                        "k": min(PROPOSAL_LIMIT, len(chosen)),
                    },
                )
            )
            work.outcome = "proposed"
            work.proposed = len(chosen)
        else:
            payloads.append(
                (
                    NO_MATCH_FOUND,
                    {
                        "match_attempt": work.command.match_attempt,
                        "reason": "no_eligible_volunteers",
                        "rejection_summary": count_rejections(work.rejections),
                    },
                )
            )
            work.outcome = "no_match"
        request.record_match(work.command.match_attempt, payloads)
        pending = request.uncommitted_events()
        if not pending:
            work.outcome = "noop"
            work.proposed = 0
            return
        self._event_store.append(
            request.aggregate_id,
            request.expected_version,
            pending,
            projector=self._projector,
        )
        request.mark_committed()

    def _today(self):
        return self._clock().date()


def _restore_match_state(request: HelpRequest, history) -> None:
    """Fold propose state from the stream so a repeated attempt is a no-op."""
    for event in history:
        if event.event_type == "HelpRequestCreated":
            request.status = "PENDING_REVIEW"
            request.concurrency_type = str(event.payload.get("concurrency_type") or "UNKNOWN")
            if event.payload.get("match_attempt") is not None:
                request.match_attempt = int(event.payload["match_attempt"])
        elif event.event_type == "ConcurrencyClassified" and event.payload.get("concurrency_type"):
            request.concurrency_type = str(event.payload["concurrency_type"])
        elif event.event_type in ("MatchesProposed", "NoMatchFound"):
            attempt = int(event.payload["match_attempt"])
            request.completed_attempts.add(attempt)
            request.match_attempt = attempt + 1
            request.status = "MATCH_PROPOSED" if event.event_type == "MatchesProposed" else "NO_MATCH"


def _unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)
