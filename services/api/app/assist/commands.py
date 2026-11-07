"""Chat commands: turn "run the search" / "go to screening" / "next step" into an application action.

Two interpreters, same output:
  * rules (always available, no AI): imperative phrases matched against a fixed vocabulary;
  * the AI model (only when the user is in "Ask AI" mode, the rules found nothing and a model is configured): it may
    only choose one of the actions listed below; anything else is discarded.

Nothing is executed here. The result names the view to open and, for actions that change data or start background
work, the existing API request to send (method, path, body), which the browser sends only after the user confirms.
So every action goes through the same endpoint, validation, rate limits and audit trail as the button it replaces.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.core.rate_limit import RateLimiter
from app.core.untrusted import fence
from app.db.models import Project, User
from app.providers.llm import LLMError, LLMGateway
from app.services import audit_service, conversation_service

MAX_TEXT = 500

# view id -> (label, words that name it). Order matters: more specific names first.
VIEWS: dict[str, tuple[str, tuple[str, ...]]] = {
    "autopilot": ("Automation", ("automation", "autopilot", "auto pilot", "pipeline")),
    "extraction": ("Extraction form", ("extraction form", "extraction schema", "extraction fields", "extraction")),
    "tables": ("Evidence table", ("evidence table", "evidence tables", "data table", "extracted data", "tables")),
    "fulltext": ("Full text", ("full text screening", "full-text screening", "full text", "full-text", "fulltext")),
    "files": ("Files", ("files", "pdfs", "pdf", "documents", "uploads", "upload")),
    "flow": ("PRISMA flow", ("prisma flow", "prisma diagram", "prisma", "flow diagram", "flow")),
    "audit": ("Audit log", ("audit log", "audit trail", "audit", "history", "activity log")),
    "protocol": ("Protocol", ("protocol", "research question", "criteria", "eligibility", "objective")),
    "search": ("Search", ("search", "searching", "search query", "search expression")),
    "papers": ("Papers", ("papers", "records", "articles", "results", "references", "library")),
    "screening": ("Screening", ("screening", "screen", "title and abstract", "abstract screening")),
    "appraisal": ("Appraisal", ("appraisal", "risk of bias", "quality assessment", "critical appraisal", "quality")),
    "synthesis": ("Synthesis", ("synthesis", "synthesise", "synthesize", "findings")),
    "report": ("Report", ("report", "manuscript", "write-up", "write up", "export")),
}
# The main path the "Next step" command and buttons follow.
FLOW = ["protocol", "search", "screening", "fulltext", "extraction", "appraisal", "synthesis", "report"]
FLOW_OF = {
    "autopilot": "protocol",
    "papers": "search",
    "files": "fulltext",
    "tables": "extraction",
    "flow": "report",
    "audit": "report",
}

ACTIONS = (
    "open_view",
    "next_step",
    "previous_step",
    "start_search",
    "suggest_search_query",
    "assess_relevance",
    "run_extraction",
    "suggest_appraisal",
    "draft_synthesis",
    "generate_report",
    "none",
)

NAV = r"(?:open|go(?: back)? to|goto|show(?: me)?|take me to|navigate to|switch to|bring up|view|display|see|jump to)"
RUN = r"(?:run|start|begin|launch|do|perform|execute|kick off|trigger|redo|re-run|rerun)"


@dataclass
class Command:
    action: str
    reply: str
    view: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    request: dict[str, Any] | None = None  # {"method", "path", "body"}; sent by the browser after confirmation
    source: str = "rules"

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reply": self.reply,
            "view": self.view,
            "params": self.params,
            "request": self.request,
            "confirm": self.request is not None,
            "source": self.source,
        }


def _norm(text: str) -> str:
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^\w\s'-]", " ", t)
    t = re.sub(
        r"\b(please|kindly|can you|could you|would you|i want to|i'd like to|i would like to|let's|lets)\b", " ", t
    )
    return " ".join(t.split())


def find_view(t: str) -> str | None:
    best: tuple[int, str] | None = None
    for view, (_, words) in VIEWS.items():
        for w in words:
            m = re.search(r"\b" + re.escape(w) + r"\b", t)
            if m and (best is None or len(w) > best[0]):
                best = (len(w), view)
    return best[1] if best else None


def step_from(view: str | None, delta: int) -> str | None:
    base = FLOW_OF.get(view or "", view)
    if base not in FLOW:
        return FLOW[0] if delta > 0 else None
    i = FLOW.index(base) + delta
    return FLOW[i] if 0 <= i < len(FLOW) else None


def interpret_rules(text: str, current_view: str | None, *, strict: bool = False) -> Command | None:
    """`strict` (protocol thread, where plain messages are answers): only unmistakable commands are recognised."""
    t = _norm(text)
    if not t:
        return None
    words = t.split()
    if re.fullmatch(
        r"(go |move |continue |proceed )?(to )?(the )?next( step)?|next step|continue to the next step", t
    ) or (not strict and re.search(r"\b(next step|move on|go next|proceed to the next)\b", t)):
        return Command("next_step", "")
    if re.fullmatch(r"(go )?back|(go |back )?(to )?(the )?previous( step)?|previous step", t) or (
        not strict and re.search(r"\b(previous step|step back|go back a step)\b", t)
    ):
        return Command("previous_step", "")
    if strict and (
        not re.match(rf"^({NAV}|{RUN}|next|previous|suggest|generate|assess|draft|create)\b", t)
        or re.match(r"^open[- ]access", t)
    ):
        return None
    if len(words) > 30:
        return None

    has_run = re.search(rf"\b{RUN}\b", t) is not None
    if re.search(r"\b(suggest|generate|build|improve|create|make|design|write|better|refine)\b", t) and re.search(
        r"\b(query|keywords?|search terms?|search expression|search string|boolean)\b", t
    ):
        return Command("suggest_search_query", "")
    if (has_run and re.search(r"\b(search|searches|literature search)\b", t)) or re.search(
        r"\b(search for papers|find (the )?(papers|articles|studies|literature)|look for papers"
        r"|search the literature)\b",
        t,
    ):
        additional = re.search(r"\b(additional|more sources|crossref|dblp|other sources|extra sources)\b", t)
        return Command("start_search", "", params={"tier": "additional" if additional else "open_access"})
    if re.search(r"\b(relevance|rank|prioriti[sz]e|ai screen|screen with ai|pre-?screen)\b", t) and (
        has_run or re.search(r"\b(assess|check|score|rank|prioriti[sz]e|screen)\b", t)
    ):
        return Command("assess_relevance", "")
    if re.search(r"\bextract(ion)?\b", t) and (has_run or re.search(r"\bextract (the )?(data|evidence|values)\b", t)):
        if not re.search(rf"\b{NAV}\b", t) or has_run:
            return Command("run_extraction", "")
    if (
        re.search(r"\b(appraise|appraisal|risk of bias|quality assessment)\b", t)
        and (has_run or re.search(r"\b(suggest|appraise|assess)\b", t))
        and not re.search(rf"^{NAV}\b", t)
    ):
        return Command("suggest_appraisal", "")
    if (
        re.search(r"\b(synthes[iz]e|synthesis|combine (the )?findings)\b", t)
        and (has_run or re.search(r"\b(draft|write|create|generate|synthes[iz]e|combine)\b", t))
        and not re.search(rf"^{NAV}\b", t)
    ):
        return Command("draft_synthesis", "")
    if re.search(r"\breport\b", t) and re.search(r"\b(generate|create|draft|write|build|make|produce)\b", t):
        return Command("generate_report", "")
    view = find_view(t)
    if view and (re.search(rf"\b{NAV}\b", t) or re.search(r"\b(where is|where are|tab|page|panel|section)\b", t)):
        return Command("open_view", "", view=view)
    if view and len(words) <= 3 and not strict:  # a bare name: "screening", "the report"
        return Command("open_view", "", view=view)
    return None


AI_SYSTEM = (
    "You map a user's chat message inside a systematic-review application to ONE application action. Choose "
    "action 'none' when the message is a question, a comment, or anything that is not clearly a request to do one "
    "of the listed actions. Never invent actions. Views: " + ", ".join(VIEWS) + ". Actions: open_view (needs view), "
    "next_step, previous_step, start_search (tier open_access or additional), suggest_search_query, assess_relevance "
    "(AI relevance screening of the found papers), run_extraction, suggest_appraisal, draft_synthesis, "
    "generate_report, none."
)
AI_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "action": {"type": "STRING", "enum": list(ACTIONS)},
        "view": {"type": "STRING"},
        "tier": {"type": "STRING"},
    },
    "required": ["action"],
}


def interpret_ai(gw: LLMGateway, text: str) -> Command | None:
    try:
        res = gw.generate_json(
            data_class="public_metadata",
            system=AI_SYSTEM,
            prompt="User message:\n" + fence(text, "message"),
            schema=AI_SCHEMA,
            temperature=0.0,
            max_output_tokens=200,
        )
    except LLMError:
        return None
    data = res.data or {}
    action = data.get("action")
    if action not in ACTIONS or action == "none":
        return None
    view = data.get("view") if data.get("view") in VIEWS else None
    if action == "open_view" and view is None:
        return None
    params = {"tier": "additional"} if action == "start_search" and data.get("tier") == "additional" else {}
    if action == "start_search":
        params.setdefault("tier", "open_access")
    return Command(str(action), "", view=view, params=params, source="ai")


def resolve(db: Session, project: Project, cmd: Command, current_view: str | None) -> Command:
    """Fill in the view to open, the confirmation text and the API request for one recognised action."""
    from app.extraction import schema_service

    pid = project.id
    base = f"/projects/{pid}"
    a = cmd.action
    if a in ("next_step", "previous_step"):
        target = step_from(current_view, 1 if a == "next_step" else -1)
        if target is None:
            cmd.reply = "This is already the first step." if a == "previous_step" else "This is the last step."
            return cmd
        cmd.view = target
        cmd.reply = f"Opening {VIEWS[target][0]}."
    elif a == "open_view" and cmd.view:
        cmd.reply = f"Opening {VIEWS[cmd.view][0]}."
    elif a == "start_search":
        tier = cmd.params.get("tier", "open_access")
        cmd.view = "search"
        label = "the additional sources" if tier == "additional" else "the open-access sources"
        cmd.reply = f"I can start a search of {label} with the query built from your approved protocol."
        cmd.request = {
            "method": "POST",
            "path": f"{base}/search-runs",
            "body": {"tier": tier, "expression": None, "providers": None},
        }
    elif a == "suggest_search_query":
        cmd.view = "search"
        cmd.reply = "I can ask the AI model to design a new Boolean search query from your protocol."
        cmd.request = {"method": "POST", "path": f"{base}/search/suggest-expression", "body": {"tier": "open_access"}}
    elif a == "assess_relevance":
        cmd.view = "screening"
        cmd.reply = "I can assess the relevance of the papers that are waiting for screening (advisory only)."
        cmd.request = {"method": "POST", "path": f"{base}/screening/relevance", "body": {"use_llm": True}}
    elif a == "run_extraction":
        cmd.view = "tables"
        schema = schema_service.current_schema(db, project)
        if schema is None:
            cmd.view = "extraction"
            cmd.reply = "There is no approved extraction form yet. Opening the form so you can review and approve it."
        else:
            cmd.reply = "I can extract the evidence from the included papers with the approved extraction form."
            cmd.request = {"method": "POST", "path": f"{base}/extraction/schemas/{schema.id}/runs", "body": {}}
    elif a == "suggest_appraisal":
        cmd.view = "appraisal"
        cmd.reply = "I can draft risk-of-bias suggestions for the included studies (you review every judgement)."
        cmd.request = {"method": "POST", "path": f"{base}/appraisal/suggestions", "body": {}}
    elif a == "draft_synthesis":
        cmd.view = "synthesis"
        schema = schema_service.current_schema(db, project)
        cmd.reply = "I can draft a synthesis of the extracted evidence."
        body: dict[str, Any] = {"schema_id": str(schema.id)} if schema is not None else {}
        cmd.request = {"method": "POST", "path": f"{base}/synthesis/drafts", "body": body}
    elif a == "generate_report":
        cmd.view = "report"
        cmd.reply = "I can generate a new draft of the review report from the current data."
        cmd.request = {"method": "POST", "path": f"{base}/reports", "body": {}}
    return cmd


def interpret(
    db: Session,
    settings: Settings,
    gw: LLMGateway | None,
    limiter: RateLimiter,
    project: Project,
    user: User,
    body: dict[str, Any],
) -> dict[str, Any]:
    text = str(body.get("text") or "").strip()
    if not text or len(text) > 20000:
        raise ValidationFailed("Send a message to interpret.", details=[{"field": "text"}])
    current = body.get("current_view") if body.get("current_view") in VIEWS else None
    strict = bool(body.get("strict"))
    cmd = interpret_rules(text[:MAX_TEXT], current, strict=strict) if len(text) <= MAX_TEXT else None
    if cmd is None and body.get("allow_ai") and not strict and gw is not None and gw.available:
        allowed, _ = limiter.hit(f"assistant:{user.id}", settings.rate_limit_assistant_per_hour, 3600)
        if allowed:
            cmd = interpret_ai(gw, text[:MAX_TEXT])
    if cmd is None:
        return {"action": "none"}
    cmd = resolve(db, project, cmd, current)
    cid = body.get("conversation_id")
    if body.get("record") and cid:
        conv_id = uuid.UUID(str(cid))
        conversation_service.get(db, project, conv_id)
        conversation_service.post_user_message(db, user, project, conv_id, content=text, client_message_id=None)
        conversation_service.append_workflow_message(
            db,
            conv_id,
            role="assistant",
            author_id=None,
            content=cmd.reply + (" Confirm below to go ahead." if cmd.request else ""),
            payload={"type": "command", "action": cmd.action, "view": cmd.view, "source": cmd.source},
            client_message_id=uuid.uuid4(),
        )
        audit_service.record(
            db,
            action="assistant.command",
            entity_type="project",
            entity_id=project.id,
            actor_id=user.id,
            project_id=project.id,
            after={"action": cmd.action, "view": cmd.view, "source": cmd.source},
        )
        db.commit()
    return cmd.as_dict()
