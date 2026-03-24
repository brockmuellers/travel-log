#!/usr/bin/env python3
"""
Experimental agentic Q&A for the travel log.

Uses an LLM with tool use to answer natural language questions about the trip
by calling the local API endpoints.

Provider selection (set PROVIDER env var):
  ollama  (default) — local Ollama; set OLLAMA_MODEL (default: qwen2.5:3b)
  gemini            — Google Gemini; set GEMINI_API_KEY and optionally GEMINI_MODEL

Both providers are accessed via the OpenAI-compatible API (pip install openai).

Tools:
  search_waypoints  — semantic search over waypoints + photos (GET /waypoints/search)
  list_waypoints    — list waypoints, optionally sorted by elevation
                      NOTE: elevation sort needs elevation_meters added to GET /waypoints

Usage:
    pip install openai

    python scripts/experiments/trip_qa.py "what purple flowers did we see"
    python scripts/experiments/trip_qa.py "where was our highest elevation stop"
    PROVIDER=gemini python scripts/experiments/trip_qa.py "did we see any penguins"
    python scripts/experiments/trip_qa.py   # runs built-in example questions
"""

import json
import os
import sys
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

_addr = os.getenv("SERVER_ADDR", "localhost:8080")
if not _addr.startswith("http"):
    _addr = f"http://localhost{_addr}" if _addr.startswith(":") else f"http://{_addr}"
SERVER_URL = _addr

SITE_TOKEN = os.getenv("SITE_TOKEN", "")
PROVIDER = os.getenv("PROVIDER", "ollama").lower()

# ── API helpers ────────────────────────────────────────────────────────────────

_session = requests.Session()
_session.headers["X-Site-Token"] = SITE_TOKEN


def _get(path: str, params: dict | None = None) -> Any:
    r = _session.get(f"{SERVER_URL}{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


# ── Tool implementations ───────────────────────────────────────────────────────

def search_waypoints(query: str, mode: str = "combined") -> str:
    """Semantic search. Returns top matching waypoints with descriptions and photo captions."""
    results = _get("/waypoints/search", {"q": query, "mode": mode})
    slim = []
    for w in results:
        entry: dict = {
            "name": w["name"],
            "description": w["description"],
            "score": round(w["score"], 1),
        }
        if w.get("photos"):
            captions = [p["caption"] for p in w["photos"] if p.get("caption")]
            if captions:
                entry["photo_captions"] = captions
        slim.append(entry)
    return json.dumps(slim, indent=2)


def list_waypoints(sort_by: str | None = None, order: str = "desc", limit: int = 5) -> str:
    """
    List waypoints. sort_by='elevation_meters' sorts by altitude.

    TODO: The current /waypoints endpoint does not return elevation_meters.
    Add elevation_meters to GET /waypoints (or a dedicated sort endpoint) to fix this.
    """
    waypoints = _get("/waypoints")

    if sort_by == "elevation_meters":
        waypoints = sorted(
            waypoints,
            key=lambda w: w.get("elevation_meters") or 0,
            reverse=(order == "desc"),
        )
        return json.dumps(
            {
                "note": (
                    "elevation_meters not in current API response — results unsorted. "
                    "Add elevation_meters to GET /waypoints to enable this."
                ),
                "waypoints": waypoints[:limit],
            },
            indent=2,
        )

    return json.dumps({"waypoints": waypoints[:limit]}, indent=2)


TOOL_FNS = {
    "search_waypoints": lambda a: search_waypoints(a["query"], a.get("mode", "combined")),
    "list_waypoints": lambda a: list_waypoints(
        a.get("sort_by"), a.get("order", "desc"), a.get("limit", 5)
    ),
}

# ── Tool schemas ───────────────────────────────────────────────────────────────
# OpenAI format — used by ollama + gemini providers.
# Anthropic uses the same parameter schemas but wraps them differently (see below).

_TOOL_PARAMS = [
    {
        "name": "search_waypoints",
        "description": (
            "Semantic search over waypoints and their photos. Best for questions about what was "
            "seen, experienced, or visited. Use mode='photo' for visual questions like 'what flowers "
            "did we see' or 'any wildlife'. Use mode='description' for place-focused questions. "
            "mode='combined' (default) blends both signals."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "mode": {
                    "type": "string",
                    "enum": ["combined", "description", "photo"],
                    "description": "Search mode: combined (default), description, or photo",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_waypoints",
        "description": (
            "List waypoints from the trip, optionally sorted. Use sort_by='elevation_meters' "
            "for altitude questions (highest/lowest point visited)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sort_by": {
                    "type": "string",
                    "enum": ["elevation_meters"],
                    "description": "Field to sort by",
                },
                "order": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "description": "desc=highest first (default), asc=lowest first",
                },
                "limit": {"type": "integer", "description": "Max results (default 5)"},
            },
        },
    },
]

# OpenAI-style tool list (ollama + gemini)
OPENAI_TOOLS = [{"type": "function", "function": t} for t in _TOOL_PARAMS]

# ── Provider backends ──────────────────────────────────────────────────────────

SYSTEM = (
    "You are answering questions about a personal trip on behalf of the people who took it. "
    "Use the available tools to look up information, then give a short, direct answer. "
    "Rules: "
    "Do not ask follow-up questions or invite further conversation. "
    "Do not quote or paraphrase photo descriptions literally — interpret them to extract facts "
    "(e.g. 'purple wildflowers near Buena Vista' not 'clusters of purple flowers with green stems'). "
    "If the data doesn't clearly answer the question, say so plainly and stop. "
    "If a tool returns a note about missing data, mention it briefly."
)


def _answer_openai(question: str, client: Any, model: str) -> str:
    """Agentic loop using the OpenAI-compatible API (ollama or gemini)."""
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": question},
    ]

    while True:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=OPENAI_TOOLS,
        )
        msg = response.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            return msg.content or ""

        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            print(f"  → {name}({json.dumps(args)})")
            result = TOOL_FNS[name](args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})


def answer(question: str) -> str:
    print(f"\n❓ {question}  [{PROVIDER}]")

    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("pip install openai")

    if PROVIDER == "ollama":
        model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
        client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

    elif PROVIDER == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        client = OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=api_key,
        )

    else:
        raise RuntimeError(f"Unknown PROVIDER={PROVIDER!r}. Choose: ollama, gemini")

    return _answer_openai(question, client, model)


# ── Main ───────────────────────────────────────────────────────────────────────

EXAMPLE_QUESTIONS = [
    "What purple flowers did we see on the trip?",
    "Where was our highest elevation location?",
    "Did we see any penguins? Where?",
    "What was the most remote place we visited?",
]

if __name__ == "__main__":
    if len(sys.argv) > 1:
        q = " ".join(sys.argv[1:])
        print(answer(q))
    else:
        for q in EXAMPLE_QUESTIONS:
            print(answer(q))
            print()
