from __future__ import annotations

import operator
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict, List, Optional, Literal, Annotated, Any, Dict, Union, cast

from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# Blog Writer (Router → (Research?) → Orchestrator → Workers → ReducerWithImages)
# Patches image capability using your 3-node reducer flow:
#   merge_content -> decide_images -> generate_and_place_images
# ============================================================


# -----------------------------
# 1) Schemas
# -----------------------------
class Task(BaseModel):
    id: int
    title: str
    goal: str = Field(..., description="One sentence describing what the reader should do/understand.")
    bullets: List[str] = Field(..., min_length=3, max_length=6)
    target_words: int = Field(..., description="Target words (120–550).")

    tags: List[str] = Field(default_factory=list)
    requires_research: bool = False
    requires_citations: bool = False
    requires_code: bool = False


class Plan(BaseModel):
    blog_title: str
    audience: str
    tone: str
    blog_kind: Literal["explainer", "tutorial", "news_roundup", "comparison", "system_design"] = "explainer"
    constraints: List[str] = Field(default_factory=list)
    tasks: List[Task]


class EvidenceItem(BaseModel):
    title: str
    url: str
    published_at: Optional[str] = None  # ISO "YYYY-MM-DD" preferred
    snippet: Optional[str] = None
    source: Optional[str] = None


class RouterDecision(BaseModel):
    needs_research: bool
    mode: Literal["closed_book", "hybrid", "open_book"]
    reason: str
    queries: List[str] = Field(default_factory=list)
    max_results_per_query: int = Field(5)


class EvidencePack(BaseModel):
    evidence: List[EvidenceItem] = Field(default_factory=list)


# ---- Image planning schema (ported from your image flow) ----
class ImageSpec(BaseModel):
    placeholder: str = Field(..., description="e.g. [[IMAGE_1]]")
    filename: str = Field(..., description="Save under images/, e.g. qkv_flow.png")
    alt: str
    caption: str
    prompt: str = Field(..., description="Prompt to send to the image model.")
    size: str = "512x512"
    quality: Literal["low", "medium", "high"] = "medium"


class GlobalImagePlan(BaseModel):
    md_with_placeholders: str
    images: List[ImageSpec] = Field(default_factory=list)

class State(TypedDict):
    topic: str

    # routing / research
    mode: str
    needs_research: bool
    queries: List[str]
    evidence: List[EvidenceItem]
    plan: Optional[Plan]

    # recency
    as_of: str
    recency_days: int

    # workers
    sections: Annotated[List[tuple[int, str]], operator.add]  # (task_id, section_md)

    # reducer/image
    merged_md: str
    md_with_placeholders: str
    image_specs: List[dict]
    generated_images: dict[str, bytes]  # Stores raw image bytes mapped by filename

    final: str
    llm_fallback_active: Annotated[bool, lambda x, y: x or y]  # Sticky flag


# -----------------------------
# 2) LLM
# -----------------------------
from openai import RateLimitError, APIError
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None

try:
    from langchain_ollama import ChatOllama
except ImportError:
    ChatOllama = None

primary_llm = ChatOpenAI(
    model="gpt-4o",
    api_key=os.getenv("OPENAI_API_KEY"),
    max_retries=0,       # Fail immediately on 429/Error
    timeout=5,           # 5 seconds timeout to switch fast
)
puter_llm = ChatOpenAI(
    base_url="https://api.puter.com/puterai/openai/v1/",
    model="qwen/qwen-2.5-72b-instruct",
    api_key=os.getenv("PUTER_AUTH_TOKEN"),
    max_retries=3,       # Be patient with Puter
    timeout=60,
)
gemini_llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    google_api_key=os.getenv("GOOGLE_API_KEY"),
    max_retries=3,
)

def get_llm_chain(schema=None, static_fallback=None):
    """
    The 'Unstoppable' Chain: OpenAI (4o -> 4o-mini) -> Puter (Qwen -> Llama) -> Gemini (Flash 1.5/2.0/Pro/Latest)
    If every single API fails and we are generating text (no schema), it resorts to 'Pseudo-Text' expansion.
    """
    from langchain_core.runnables import RunnableLambda
    import time
    import random

    def _invoke_with_fallback(input_params):
        errors: List[str] = []
        fallback_used = False
        
        # 1. OpenAI Layer (Try 4o only)
        try:
            llm_m = ChatOpenAI(model="gpt-4o", api_key=os.getenv("OPENAI_API_KEY"), timeout=10, max_retries=0)
            chain = llm_m.with_structured_output(schema, method="json_mode") if schema else llm_m
            return chain.invoke(input_params)
        except Exception as e:
            errors.append(f"OpenAI (gpt-4o): {e}")

        fallback_used = True

        # 2. Puter Layer (Qwen)
        try:
            llm_p = ChatOpenAI(
                base_url="https://api.puter.com/puterai/openai/v1/",
                model="qwen/qwen-2.5-72b-instruct",
                api_key=os.getenv("PUTER_AUTH_TOKEN"),
                timeout=15,
                max_retries=0
            )
            if schema:
                hint = "Respond ONLY with valid JSON."
                params = list(input_params) + [HumanMessage(content=hint)] if isinstance(input_params, list) else f"{input_params}\n\n{hint}"
                chain = llm_p.with_structured_output(schema, method="json_mode")
            else:
                params = input_params
                chain = llm_p
            res = chain.invoke(params)
            if hasattr(res, "additional_kwargs"):
                res.additional_kwargs["llm_fallback_active"] = True
            return res
        except Exception as e:
            errors.append(f"Puter (Qwen): {e}")

        # 3. Gemini Layer (Flash)
        try:
            if not ChatGoogleGenerativeAI:
                raise ImportError("langchain_google_genai not installed")
            g_llm = ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                google_api_key=os.getenv("GOOGLE_API_KEY"),
                max_retries=0,
                timeout=15
            )
            chain = g_llm.with_structured_output(schema) if schema else g_llm
            res = chain.invoke(input_params)
            if hasattr(res, "additional_kwargs"):
                res.additional_kwargs["llm_fallback_active"] = True
            return res
        except Exception as e:
            errors.append(f"Gemini (flash-1.5): {e}")

        # 4. Offline Layer (Ollama: tinyllama)
        try:
            if not ChatOllama:
                raise ImportError("langchain_ollama not installed")
            
            print(f"🤖 Trying Ollama (qwen2.5:0.5b) fallback... (timeout=300s)")
            o_llm = ChatOllama(model="qwen2.5:0.5b", timeout=300)
            if schema:
                hint = f"Respond ONLY with valid JSON matching this schema: {schema.schema_json()}"
                params = list(input_params) + [HumanMessage(content=hint)] if isinstance(input_params, list) else f"{input_params}\n\n{hint}"
                chain = o_llm
            else:
                params = input_params
                chain = o_llm
            
            res = chain.invoke(params)
            print("✅ Ollama responded successfully.")
            if hasattr(res, "additional_kwargs"):
                res.additional_kwargs["llm_fallback_active"] = True
            
            if schema:
                if hasattr(res, "content"):
                    import json
                    try:
                        # Find the first { and last }
                        start = res.content.find("{")
                        end = res.content.rfind("}") + 1
                        if start >= 0 and end > start:
                            obj = json.loads(res.content[start:end])
                            return schema.model_validate(obj)
                    except Exception:
                        pass
                # If we have a schema but couldn't parse JSON, this model failed to provide structured output
                raise ValueError("Ollama (tinyllama) failed to provide valid JSON for schema")
            return res
        except Exception as e:
            errors.append(f"Ollama (tinyllama): {e}")

        # 5. Emergency Fallbacks
        if static_fallback is not None:
            print("🚨 ALL AI FAILED. Using Emergency Static Object.")
            # We can't easily tag a Pydantic object with fallback_active unless we modify it
            # But the nodes will set it if they catch an exception or see this
            return static_fallback
            
        if not schema:
            # Pseudo-Writer: Extract and format content from the prompt metadata
            print("🚨 ALL AI FAILED. Using Pseudo-Writer Fallback.")
            
            # 1. Extract raw content string from messages if needed
            if isinstance(input_params, list):
                from langchain_core.messages import HumanMessage
                content_sources = [m.content for m in input_params if isinstance(m, HumanMessage)]
                content_str = "\n".join(content_sources) if content_sources else str(input_params)
            else:
                content_str = str(input_params)
            
            # 2. Extract Title
            title_search = re.search(r"Section title: (.*)", content_str)
            title_text = title_search.group(1).strip() if title_search else "Introduction"
            
            # 3. Extract content bullets (distinguish from metadata and evidence)
            # Find the "Bullets:" section and capture everything until "Evidence"
            bullets_part = ""
            bullets_match = re.search(r"Bullets:(.*?)(?:Evidence|$)", content_str, re.DOTALL)
            if bullets_match:
                bullets_part = bullets_match.group(1)
            
            # If "Bullets:" header not found, fall back to general bullet search but exclude metadata-like lines
            raw_bullets = re.findall(r"^- (.*)", bullets_part or content_str, re.MULTILINE)
            content_bullets = []
            for b in raw_bullets:
                b = b.strip()
                # Skip evidence lines (contain http or |) and metadata-like keys
                if "http" in b or "|" in b or b.endswith(":") or not b:
                    continue
                content_bullets.append(b)
            
            # 4. Format into readable text
            if content_bullets:
                sentences = []
                for b in content_bullets:
                    s = b[0].upper() + b[1:] if len(b) > 0 else b
                    if not s.endswith(('.', '!', '?')): s += "."
                    sentences.append(s)
                body = " ".join(sentences)
                msg = AIMessage(content=f"## {title_text}\n\n{body}")
            else:
                msg = AIMessage(content=f"## {title_text}\n\nContent generation failed due to API limits. Summary: {title_text} is important for this topic.")
            
            msg.additional_kwargs["llm_fallback_active"] = True
            return msg

        errors_trace = "\n".join(cast(Any, errors)[-5:])
        raise Exception(f"Total Quota Exhaustion across all available model providers (OpenAI, Puter, Gemini, Ollama). Trace:\n{errors_trace}")

    return RunnableLambda(_invoke_with_fallback)

# Default fallback chain for simple completions
llm = get_llm_chain()

# -----------------------------
# 3) Router
# -----------------------------
ROUTER_SYSTEM = """You are a JSON routing module. Return ONLY valid JSON matching this schema:
{
  "needs_research": boolean,
  "mode": "closed_book" | "hybrid" | "open_book",
  "reason": string,
  "queries": string[],
  "max_results_per_query": number
}

CRITICAL: The 'mode' field MUST be exactly one of: 'closed_book', 'hybrid', 'open_book'.

Modes:
- closed_book: evergreen concepts.
- hybrid: evergreen + needs up-to-date examples.
- open_book: volatile news/latest updates.
"""

def router_node(state: State) -> dict:
    decider = get_llm_chain(
        RouterDecision,
        static_fallback=RouterDecision(
            needs_research=False,
            mode="closed_book",
            reason="LLM quota exhausted fallback",
            queries=[],
            max_results_per_query=5
        )
    )
    decision = decider.invoke(
        [
            SystemMessage(content=ROUTER_SYSTEM),
            HumanMessage(content=f"Topic: {state['topic']}\nAs-of date: {state['as_of']}\nReturn JSON."),
        ]
    )

    is_fallback = False
    if hasattr(decision, "additional_kwargs") and decision.additional_kwargs.get("llm_fallback_active"):
        is_fallback = True
    elif isinstance(decision, RouterDecision) and decision.reason == "LLM quota exhausted fallback":
        is_fallback = True

    if decision.mode == "open_book":
        recency_days = 7
    elif decision.mode == "hybrid":
        recency_days = 45
    else:
        recency_days = 3650

    return {
        "needs_research": decision.needs_research,
        "mode": decision.mode,
        "queries": decision.queries,
        "recency_days": recency_days,
        "llm_fallback_active": is_fallback
    }

def route_next(state: State) -> str:
    return "research" if state["needs_research"] else "orchestrator"

# -----------------------------
# 4) Research (Tavily)
# -----------------------------
def _tavily_search(query: str, max_results: int = 5) -> List[dict]:
    if not os.getenv("TAVILY_API_KEY"):
        return []
    try:
        from langchain_community.tools.tavily_search import TavilySearchResults  # type: ignore
        tool = TavilySearchResults(max_results=max_results)
        results = tool.invoke({"query": query})
        out: List[dict] = []
        for r in results or []:
            out.append(
                {
                    "title": r.get("title") or "",
                    "url": r.get("url") or "",
                    "snippet": r.get("content") or r.get("snippet") or "",
                    "published_at": r.get("published_date") or r.get("published_at"),
                    "source": r.get("source"),
                }
            )
        return out
    except Exception:
        return []

def _iso_to_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None

RESEARCH_SYSTEM = """You are a JSON research synthesizer. Return ONLY valid JSON matching this schema:
{
  "evidence": [
    {
      "title": string,
      "url": string,
      "published_at": string | null,
      "snippet": string,
      "source": string | null
    }
  ]
}

Rules:
- Only include items with a non-empty url.
- Prefer relevant + authoritative sources.
- Normalize published_at to ISO YYYY-MM-DD if reliably inferable; else null.
"""

def research_node(state: State) -> dict:
    queries = cast(Any, state.get("queries") or [])[:10]
    raw: List[dict] = []
    for q in queries:
        raw.extend(_tavily_search(q, max_results=6))

    if not raw:
        return {"evidence": []}

    extractor = get_llm_chain(EvidencePack)
    pack = extractor.invoke(
        [
            SystemMessage(content=RESEARCH_SYSTEM),
            HumanMessage(
                content=(
                    f"As-of date: {state['as_of']}\n"
                    f"Recency days: {state['recency_days']}\n\n"
                    f"Raw results:\n{raw}\n\n"
                    "Return JSON."
                )
            ),
        ]
    )

    dedup = {}
    for e in pack.evidence:
        if e.url:
            dedup[e.url] = e
    evidence = list(dedup.values())

    if state.get("mode") == "open_book":
        as_of = date.fromisoformat(state["as_of"])
        cutoff = as_of - timedelta(days=int(state["recency_days"]))
        evidence = [e for e in evidence if (d := _iso_to_date(e.published_at)) and d >= cutoff]

    return {"evidence": evidence}

# -----------------------------
# 5) Orchestrator (Plan)
# -----------------------------
ORCH_SYSTEM = """You are a JSON technical blog planner. Return ONLY valid JSON matching this schema:
{
  "blog_title": string,
  "audience": string,
  "tone": string,
  "blog_kind": "explainer" | "tutorial" | "news_roundup" | "comparison" | "system_design",
  "constraints": string[],
  "tasks": [
    {
      "id": number,
      "title": string,
      "goal": string,
      "bullets": string[],
      "target_words": number,
      "tags": string[],
      "requires_research": boolean,
      "requires_citations": boolean,
      "requires_code": boolean
    }
  ]
}

CRITICAL: WORD COUNT BUDGET: Sum of 'target_words' MUST match 'TOTAL WORD BUDGET' exactly.
CRITICAL: Do NOT plan more than 'MAX SECTIONS'.
"""

def orchestrator_node(state: State) -> dict:
    planner = get_llm_chain(
        Plan,
        static_fallback=Plan(
            blog_title=state['topic'],
            audience="General",
            tone="Informative",
            blog_kind="explainer",
            constraints=["Standard blog overview"],
            tasks=[
                Task(id=1, title="Introduction to " + state['topic'], goal="Provide an overview", bullets=["Definition", "Context", "Significance"], target_words=300),
                Task(id=2, title="Core Principles", goal="Explain the main topic", bullets=["Key concept 1", "Key concept 2", "Key concept 3"], target_words=500),
                Task(id=3, title="Future Outlook", goal="Wrap up", bullets=["Summary", "Implications", "Conclusion"], target_words=200)
            ]
        )
    )
    mode = state.get("mode", "closed_book")
    evidence = state.get("evidence", [])

    forced_kind = "news_roundup" if mode == "open_book" else None

    # Programmatic word count extraction
    topic = state['topic']
    word_match = re.search(r"(\d+)\s*words?", topic, re.IGNORECASE)
    total_budget = int(word_match.group(1)) if word_match else 1000
    
    max_sections = 5
    if total_budget < 400:
        max_sections = 2
    elif total_budget < 700:
        max_sections = 3

    plan = planner.invoke(
        [
            SystemMessage(content=ORCH_SYSTEM),
            HumanMessage(
                content=(
                    f"Topic: {topic}\n"
                    f"TOTAL WORD BUDGET: {total_budget}\n"
                    f"MAX SECTIONS: {max_sections}\n"
                    f"Mode: {mode}\n"
                    f"Return JSON."
                )
            ),
        ]
    )
    
    # --- HARD ENFORCEMENT ---
    # 1. Enforce Max Sections
    if len(plan.tasks) > max_sections:
        plan.tasks = plan.tasks[:max_sections]
    
    # 2. Scale Word Budgets to match Total Budget
    current_total = sum(t.target_words for t in plan.tasks)
    if current_total > 0:
        multiplier = total_budget / current_total
        for t in plan.tasks:
            t.target_words = int(t.target_words * multiplier)
    
    # 3. Prevent meta-commentary titles
    for t in plan.tasks:
        t.title = t.title.replace(topic, "").strip(" :")
        if not t.title: t.title = "Overview"

    is_fallback = state.get("llm_fallback_active", False)
    if hasattr(plan, "additional_kwargs") and plan.additional_kwargs.get("llm_fallback_active"):
        is_fallback = True
    elif plan.blog_title == state['topic'] and "LLM quota exhausted fallback" in str(plan.constraints): # Simple heuristic if static fallback used
        is_fallback = True

    return {"plan": plan, "llm_fallback_active": is_fallback}


# -----------------------------
# 6) Fanout
# -----------------------------
def fanout(state: State):
    assert state["plan"] is not None
    return [
        Send(
            "worker",
            {
                "task": task.model_dump(),
                "topic": state["topic"],
                "mode": state["mode"],
                "as_of": state["as_of"],
                "recency_days": state["recency_days"],
                "plan": state["plan"].model_dump(),
                "evidence": [e.model_dump() for e in state.get("evidence", [])],
            },
        )
        for task in state["plan"].tasks
    ]

# -----------------------------
# 7) Worker
# -----------------------------
WORKER_SYSTEM = """You are a robotic technical writing machine. 
ONLY output the requested section markdown starting with a level 2 header (e.g. "## Your Section Name").

STRICT CONSTRAINTS (VIOLATION RESULTS IN TERMINATION):
1. **NO INTROS/OUTROS**: NEVER say "Hello", "Welcome", "In this section", "I am excited", "I have researched", "Thank you", or "Conclusion".
2. **FACTUAL ACCURACY**: UPSC stands for Union Public Service Commission. It is for Indian Civil Services (IAS, IPS, IFS). It is NOT for IITs, engineering, or medical entrance.
3. **STRICT WORD BUDGET**: Stay within ±5% of the 'Target words'. Every extra word wastes the user's budget.
4. **NO REPETITION**: If you already mentioned a fact, do NOT repeat it. 
5. **NO LINKS/CODE**: Pure text ONLY.
"""

def worker_node(payload: dict) -> dict:
    task = Task(**payload["task"])
    plan = Plan(**payload["plan"])
    evidence = [EvidenceItem(**e) for e in payload.get("evidence", [])]

    bullets_text = "\n- " + "\n- ".join(task.bullets)
    evidence_text = "\n".join(
        f"- {e.title} | {e.url} | {e.published_at or 'date:unknown'}"
        for e in evidence[:20]
    )

    # We add a static fallback for the worker too
    worker_llm = get_llm_chain(
        static_fallback=AIMessage(content="Content generation failed for this section due to total LLM quota exhaustion. Please try again later.")
    )

    res = worker_llm.invoke(
        [
            SystemMessage(content=WORKER_SYSTEM),
            HumanMessage(
                content=(
                    f"Blog title: {plan.blog_title}\n"
                    f"Audience: {plan.audience}\n"
                    f"Tone: {plan.tone}\n"
                    f"Blog kind: {plan.blog_kind}\n"
                    f"Constraints: {plan.constraints}\n"
                    f"Topic: {payload['topic']}\n"
                    f"Mode: {payload.get('mode')}\n"
                    f"As-of: {payload.get('as_of')} (recency_days={payload.get('recency_days')})\n\n"
                    f"Section title: {task.title}\n"
                    f"Goal: {task.goal}\n"
                    f"Target words: {task.target_words}\n"
                    f"Tags: {task.tags}\n"
                    f"requires_research: {task.requires_research}\n"
                    f"requires_citations: False\n"
                    f"requires_code: False\n"
                    f"Bullets:{bullets_text}\n\n"
                    f"Grounding Data (DO NOT cite or list these):\n{evidence_text}\n"
                )
            ),
        ]
    )
    section_md = res.content.strip()
    
    is_fallback = payload.get("llm_fallback_active", False)
    if hasattr(res, "additional_kwargs") and res.additional_kwargs.get("llm_fallback_active"):
        is_fallback = True

    return {"sections": [(task.id, section_md)], "llm_fallback_active": is_fallback}

# ============================================================
# 8) ReducerWithImages (subgraph)
#    merge_content -> decide_images -> generate_and_place_images
# ============================================================
def merge_content(state: State) -> dict:
    plan = state["plan"]
    if plan is None:
        raise ValueError("merge_content called without plan.")
    ordered_sections = [md for _, md in sorted(state["sections"], key=lambda x: x[0])]
    body = "\n\n".join(ordered_sections).strip()
    
    # Post-processing to ensure NO links are in the output as requested by user
    # 1. Convert markdown links [text](url) to just 'text'
    body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    # 2. Remove raw http/https URLs (avoiding those inside code blocks might be tricky, 
    # but the user was very clear: "dont add links")
    body = re.sub(r"https?://\S+", "", body)
    
    # 3. Aggressive Header/Meta Cleanup
    bad_headers = [
        "Evidence", "References?", "Further Reading", "Grounding Data", "Thank You", 
        "Requirements", "Knowledge Base", "Business Applications", "Source Code",
        "Blog Post", "Background Information", "Advantages for Industries", "Disadvantages",
        "Code snippet", "Example", "Bullets", "Roadmap", "Conclusion", "Summary", "Implications"
    ]
    for bh in bad_headers:
        body = re.sub(rf"(?i)^#*\s*{bh}:?.*$", "", body, flags=re.MULTILINE)
    
    # 4. Remove meta-commentary, research mentions, and placeholders
    meta_patterns = [
        r"(?i)\(DO NOT cite or list these\)",
        r"(?i)Please note:.*",
        r"(?i)Stay tuned for.*",
        r"(?i)I (am proud to say|have done|researched|consulted).*?research.*",
        r"(?i)This (blog post|section) (provides|contains|is significantly).*?overview.*",
        r"(?i)The focus here is on.*?rather than.*",
        r"(?i)Remember, technology is advancing.*",
        r"(?i)I hope this summary.*",
        r"(?i)Leaving a comment while you're working.*"
    ]
    for pattern in meta_patterns:
        body = re.sub(pattern, "", body)
    
    # Remove bracketed link placeholders [Link], [Reference Link 1], etc.
    body = re.sub(r"\[[A-Za-z\s]*\d?\](\s*\(\s*[^)]*\s*\))?", "", body)
    
    # 5. Cross-Section Deduplication (Paragraph level)
    paragraphs = body.split("\n\n")
    unique_paragraphs = []
    seen_content = set()
    for p in paragraphs:
        p = p.strip()
        if not p: continue
        # Clean paragraph for comparison
        clean_p = re.sub(r"\W+", "", p).lower()
        if not clean_p or len(clean_p) < 40: # Allow short headers or transitions
            unique_paragraphs.append(p)
            continue
        if clean_p in seen_content:
            continue
        seen_content.add(clean_p)
        unique_paragraphs.append(p)
    body = "\n\n".join(unique_paragraphs).strip()

    # 6. Deduplicate Headers
    lines = body.splitlines()
    new_lines = []
    seen_headers = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            header = stripped[3:].lower().strip(" :")
            # Remove redundant "in 300 words" from headers
            header = re.sub(r"in \d+ words", "", header).strip()
            if header in seen_headers:
                continue 
            seen_headers.add(header)
            new_lines.append(f"## {header.title()}")
        else:
            new_lines.append(line)
    body = "\n".join(new_lines).strip()

    # 7. Final Hard Pruning
    topic = state.get("topic", "")
    word_match = re.search(r"(\d+)\s*words?", str(topic), re.IGNORECASE)
    if word_match:
        budget = int(word_match.group(1))
        words_only = body.split()
        if len(words_only) > budget:
            target_words = int(budget * 1.1)
            matches = list(re.finditer(r'\S+', body))
            if len(matches) > target_words:
                cutoff_index = matches[target_words].end()
                body = body[:cutoff_index]
                last_period = body.rfind(".")
                if last_period > len(body) * 0.8:
                    body = body[:last_period + 1]
    
    merged_md = f"{body}\n"
    return {"merged_md": merged_md, "final": merged_md}


DECIDE_IMAGES_SYSTEM = """You are an expert technical editor. Return ONLY valid JSON matching this schema:
{
  "md_with_placeholders": string,
  "images": [
    {
      "placeholder": string,
      "filename": string,
      "alt": string,
      "caption": string,
      "prompt": string,
      "size": "256x256" | "512x512" | "1024x1024" | "1024x1792" | "1792x1024",
      "quality": "low" | "medium" | "high"
    }
  ]
}

CRITICAL: Max 3 images total. Placeholders must be exactly: [[IMAGE_1]], [[IMAGE_2]], [[IMAGE_3]].
CRITICAL: Placeholders MUST be on their own line, separated from other text by a blank line. Do NOT wrap them in Markdown link or image syntax.
CRITICAL: Preferred size is 512x512 unless it is a very detailed diagram or panorama.
"""

def decide_images(state: State) -> dict:
    if state.get("llm_fallback_active"):
        print("🚨 Fallback active. Skipping image planning.")
        return {
            "md_with_placeholders": state["merged_md"],
            "image_specs": [],
        }

    try:
        planner = get_llm_chain(GlobalImagePlan)
        merged_md = state["merged_md"]
        plan = state["plan"]
        assert plan is not None

        image_plan = planner.invoke(
            [
                SystemMessage(content=DECIDE_IMAGES_SYSTEM),
                HumanMessage(
                    content=(
                        f"Blog kind: {plan.blog_kind}\n"
                        f"Topic: {state['topic']}\n\n"
                        "Insert placeholders + propose image prompts.\n\n"
                        f"{merged_md}\n\n"
                        "Return JSON."
                    )
                ),
            ]
        )

        return {
            "md_with_placeholders": image_plan.md_with_placeholders,
            "image_specs": [img.model_dump() for img in image_plan.images],
        }
    except Exception as e:
        print(f"⚠️ decide_images failed: {e}. Falling back to text-only mode.")
        return {
            "md_with_placeholders": state["merged_md"],
            "image_specs": [],
        }


def _resize_image_bytes(img_bytes: bytes, target_size_str: str, quality: int = 80) -> bytes:
    """
    Resizes image bytes to the target size and applies quality compression.
    target_size_str: e.g. "512x512"
    quality: 1-100 (for WebP/JPEG)
    """
    from PIL import Image
    import io

    try:
        w, h = map(int, target_size_str.split("x"))
    except Exception:
        w, h = 512, 512

    img = Image.open(io.BytesIO(img_bytes))
    
    # Use LANCZOS for high-quality downsampling
    img = img.resize((w, h), Image.Resampling.LANCZOS)
    
    out_io = io.BytesIO()
    # Save as WebP for best size/quality ratio. Fallback to JPEG if needed.
    # WebP is widely supported in modern browsers (Streamlit).
    try:
        img.save(out_io, format="WEBP", quality=quality, method=6)
    except Exception:
        # Fallback to JPEG if WEBP is not available in the Pillow installation
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(out_io, format="JPEG", quality=quality, optimize=True)
        
    return out_io.getvalue()
# 
# 
def _fetch_fallback_image_bytes(keywords: str, size: str = "512x512") -> bytes:
    """
    Fetches a fallback image from a public service (LoremFlickr).
    """
    import urllib.request
    import urllib.parse
    
    # Clean keywords: take first 3 words, remove non-alphanumeric
    clean_kws = re.sub(r"[^a-zA-Z0-9 ]", "", keywords)
    search_term = ",".join(clean_kws.split()[:3]) or "technology"
    
    try:
        w, h = map(int, size.split("x"))
    except Exception:
        w, h = 512, 512

    url = f"https://loremflickr.com/{w}/{h}/{urllib.parse.quote(search_term)}"
    
    try:
        # Use a user-agent to avoid being blocked
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read()
    except Exception as e:
        raise RuntimeError(f"Fallback image fetch failed: {e}")
# 
# 
def _search_real_image_url(query: str) -> Optional[str]:
    """
    Searches for a real image URL using Tavily.
    """
    api_key = os.environ.get("TAVILY_API_KEY") or os.environ.get("TVLY_API_KEY")
    if not api_key:
        return None
    
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        # Search for images specifically
        result = client.search(query=query, search_depth="advanced", include_images=True)
        images = result.get("images", [])
        if images:
            # Return the first image URL
            return images[0]
    except Exception as e:
        print(f"⚠️ Tavily image search failed: {e}")
    return None
# 
# 
def _download_image_bytes(url: str) -> bytes:
    """
    Downloads image bytes from a URL.
    """
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.content

def _gemini_generate_image_bytes(prompt: str, size: str = "1024x1024") -> bytes:
    """
    Returns raw image bytes generated by Google Gemini (Imagen 3).
    Requires: pip install google-genai
    Env var: GOOGLE_API_KEY
    """
    from google import genai
    from google.genai import types
    import io

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set.")

    client = genai.Client(api_key=api_key)

    try:
        # Map aspect ratios for Imagen 3
        # Imagen 3 supports: "1:1", "4:3", "3:4", "16:9", "9:16"
        try:
            target_w, target_h = map(int, size.split("x"))
            ratio = target_w / target_h
            if 0.9 <= ratio <= 1.1:
                aspect_ratio = "1:1"
            elif 1.2 <= ratio <= 1.4:
                aspect_ratio = "4:3"
            elif 0.7 <= ratio <= 0.8:
                aspect_ratio = "3:4"
            elif 1.7 <= ratio <= 1.8:
                aspect_ratio = "16:9"
            elif 0.5 <= ratio <= 0.6:
                aspect_ratio = "9:16"
            else:
                aspect_ratio = "1:1"
        except Exception:
            aspect_ratio = "1:1"

        print(f"🎨 Generating {aspect_ratio} image with Gemini Imagen 3")
        
        response = client.models.generate_images(
            model='imagen-4.0-generate-001',
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                output_mime_type='image/png'
            )
        )
        
        if not response.generated_images:
            raise RuntimeError("Gemini returned no images.")
            
        gen_img = response.generated_images[0]
        
        # gen_img.image contains the bytes if we used output_mime_type
        # or it might be a PIL Image object depending on the SDK version
        if hasattr(gen_img.image, 'image_bytes'):
            return gen_img.image.image_bytes
        else:
            # Fallback for PIL-like object
            img_byte_arr = io.BytesIO()
            gen_img.image.save(img_byte_arr, format='PNG')
            return img_byte_arr.getvalue()

    except Exception as e:
        print(f"⚠️ Gemini image generation failed: {e}")
        raise e

def _safe_slug(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9 _-]+", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s or "blog"


def generate_and_place_images(state: State) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    import io
    from PIL import Image

    md = state.get("md_with_placeholders") or state["merged_md"]
    
    if state.get("llm_fallback_active"):
        print("🚨 Fallback active. Skipping image generation.")
        return {"final": md, "generated_images": {}}

    plan = state["plan"]
    assert plan is not None
    image_specs = state.get("image_specs", []) or []
    generated_images = {}

    def _process_single_image(spec):
        placeholder = spec["placeholder"]
        orig_filename = spec["filename"]
        filename = Path(orig_filename).stem + ".webp"
        target_size = spec.get("size", "512x512")
        img_bytes = None

        # 1. Try real image search
        try:
            print(f"🔍 Searching: {spec['prompt']}")
            img_url = _search_real_image_url(spec["prompt"])
            if img_url:
                img_bytes = _download_image_bytes(img_url)
        except Exception as e:
            print(f"⚠️ Search failed for {placeholder}: {e}")

        # 2. Try Gemini fallback
        if not img_bytes:
            try:
                print(f"🎨 Gemini Fallback: {spec['prompt']}")
                img_bytes = _gemini_generate_image_bytes(spec["prompt"], size=spec.get("size", "1024x1024"))
            except Exception as e:
                print(f"⚠️ Gemini failed for {placeholder}: {e}")

        # 3. Last resort: LoremFlickr
        if not img_bytes:
            try:
                print(f"🖼️ LoremFlickr Fallback: {spec['prompt']}")
                img_bytes = _fetch_fallback_image_bytes(spec["prompt"], size=target_size)
            except Exception as fe:
                print(f"❌ All sources failed for {placeholder}: {fe}")
                return placeholder, None, filename

        # 4. Resize and Optimize
        if img_bytes:
            try:
                # Use a default quality of 80 since it's most common
                img_bytes = _resize_image_bytes(img_bytes, target_size, quality=80)
                return placeholder, img_bytes, filename
            except Exception as ree:
                print(f"⚠️ Resize failed for {filename}: {ree}")
                return placeholder, None, filename
        
        return placeholder, None, filename

    # Run processing in parallel
    print(f"🚀 Processing {len(image_specs)} images in parallel...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(_process_single_image, image_specs))

    # Apply results back to MD and state
    final_md = str(md)
    for placeholder, img_bytes, filename in results:
        # Find the original spec to get alt/caption
        target_spec = next((s for s in image_specs if s["placeholder"] == placeholder), {})
        
        if img_bytes:
            generated_images[filename] = img_bytes
            alt_text = target_spec.get('alt', 'image')
            caption_text = target_spec.get('caption', '') or alt_text
            img_md = f"\n\n![{alt_text}](images/{filename})\n*{caption_text}*\n\n"
        else:
            caption_text = target_spec.get('caption', '')
            prompt_text = target_spec.get('prompt', '')
            img_md = (
                f"\n\n> **[IMAGE FETCH FAILED]** {caption_text}\n"
                f"> **Prompt:** {prompt_text}\n\n"
            )
        
        pattern = rf"\[* ?{re.escape(placeholder)} ?\]*"
        final_md = re.sub(pattern, img_md, final_md)

    return {"final": final_md, "generated_images": generated_images}

# build reducer subgraph
reducer_graph = StateGraph(State)
reducer_graph.add_node("merge_content", merge_content)
reducer_graph.add_node("decide_images", decide_images)
reducer_graph.add_node("generate_and_place_images", generate_and_place_images)
reducer_graph.add_edge(START, "merge_content")
reducer_graph.add_edge("merge_content", "decide_images")
reducer_graph.add_edge("decide_images", "generate_and_place_images")
reducer_graph.add_edge("generate_and_place_images", END)
reducer_subgraph = reducer_graph.compile()

# -----------------------------
# 9) Build main graph
# -----------------------------
g = StateGraph(State)
g.add_node("router", router_node)
g.add_node("research", research_node)
g.add_node("orchestrator", orchestrator_node)
g.add_node("worker", worker_node)
g.add_node("reducer", reducer_subgraph)

g.add_edge(START, "router")
g.add_conditional_edges("router", route_next, {"research": "research", "orchestrator": "orchestrator"})
g.add_edge("research", "orchestrator")

g.add_conditional_edges("orchestrator", fanout, ["worker"])
g.add_edge("worker", "reducer")
g.add_edge("reducer", END)

app = g.compile()
app
