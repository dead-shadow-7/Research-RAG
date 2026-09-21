"""Generation: retrieved chunks -> an OpenAI-compatible model -> streamed answer.

Citations
---------
Anthropic's `search_result` blocks let the model attribute its own claims with verified
quoted spans. No OpenAI-compatible endpoint has an equivalent, so sources are numbered
in the prompt and the model is asked to mark claims with `[n]`.

That shifts the trust model: the marker is the model's assertion, not the provider's.
Two guards keep it honest:
  * a marker whose number is not in the supplied range is dropped, so a citation can
    never point at a document that was not retrieved;
  * markers are stripped from the visible text and re-emitted as `citation` events, so
    the UI renders them from validated data rather than from model output.

What is *not* guaranteed is that the cited passage actually supports the sentence -- only
that the passage was in front of the model. Hence no `cited_text` is emitted here.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.schemas import ChatTurn

SYSTEM_PROMPT = """You are a retrieval-grounded research assistant.

Answer strictly from the numbered sources supplied with the question. Rules:
- Cite with a bracketed source number immediately after the claim it supports, like \
this: The warranty runs for 36 months [2].
- Only ever cite a number that appears in the sources you were given.
- If the sources do not contain the answer, say so plainly. Never fill gaps from \
background knowledge, and never guess.
- Prefer a direct answer first, then supporting detail.
- If sources conflict, say so and present both.
- Do not mention "chunks", "context", "sources" or the retrieval machinery; speak \
about the documents themselves.
"""

# Matches a citation marker, including grouped forms like [1,3] or [2][4].
_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
# A trailing fragment that might still grow into a marker, e.g. "[1" or "[12,".
_PARTIAL = re.compile(r"\[[\d,\s]*$")

SNIPPET_CHARS = 200


def get_llm() -> ChatOpenAI:
    if not settings.openai_model:
        raise RuntimeError("OPENAI_MODEL is not set -- set it to your provider's model id")

    extra_body: dict[str, Any] = {}
    if settings.llm_reasoning == "off":
        # OpenRouter-style switch, verified against this gateway: it takes Qwen3's
        # thinking pass to zero tokens. Providers that don't recognise the field
        # ignore it, so this is safe to send unconditionally when reasoning is off.
        extra_body["reasoning"] = {"enabled": False}

    return ChatOpenAI(
        model=settings.openai_model,
        # Blank base_url means api.openai.com; any other provider sets it in .env.
        base_url=settings.openai_base_url or None,
        api_key=settings.openai_api_key or "unset",
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
        extra_body=extra_body or None,
        streaming=True,
    )


def format_sources(docs: list[Document]) -> str:
    """Number the sources so the model has something stable to cite."""
    blocks = []
    for i, doc in enumerate(docs, start=1):
        meta = doc.metadata
        page = meta.get("page_from")
        label = meta.get("title", "Untitled")
        if page is not None:
            label = f"{label}, page {page}"
        blocks.append(f"[{i}] {label}\n{doc.page_content}")
    return "\n\n".join(blocks)


def source_ref(doc: Document, number: int) -> dict[str, Any]:
    """The payload behind one marker. Built from retrieval, never from model output."""
    meta = doc.metadata
    page = meta.get("page_from")
    title = meta.get("title", "Untitled")
    return {
        "number": number,
        "source": meta.get("vector_id", f"doc:{meta.get('document_id')}"),
        "title": f"{title} (p.{page})" if page is not None else title,
        "page_from": page,
        "snippet": doc.page_content[:SNIPPET_CHARS],
    }


def build_messages(
    query: str, docs: list[Document], history: list[ChatTurn] | None = None
) -> list[BaseMessage]:
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]

    for turn in history or []:
        if turn.role == "user":
            messages.append(HumanMessage(content=turn.content))
        else:
            messages.append(AIMessage(content=turn.content))

    messages.append(
        HumanMessage(content=f"Sources:\n\n{format_sources(docs)}\n\nQuestion: {query}")
    )
    return messages


def _text_of(chunk: Any) -> str:
    """Content is a string on most providers, but some return a block list."""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


async def stream_answer(
    query: str, docs: list[Document], history: list[ChatTurn] | None = None
) -> AsyncIterator[dict[str, Any]]:
    """Yield `{"type": "token"|"citation"|"usage", ...}` events.

    Markers are parsed out of the stream, so a marker split across two chunks
    ("...[1" then "2]...") is held back rather than leaking into the visible text.
    """
    llm = get_llm()
    messages = build_messages(query, docs, history)

    buffer = ""
    seen: set[int] = set()

    async for chunk in llm.astream(messages):
        buffer += _text_of(chunk)

        # Emit completed markers, and the text that precedes them.
        while (match := _MARKER.search(buffer)) is not None:
            before, buffer = buffer[: match.start()], buffer[match.end() :]
            # Models write "dispatch [1]." -- dropping the space keeps the marker
            # hugging the word it supports and stops " ." appearing in the plain
            # text that gets replayed as conversation history.
            before = before.rstrip(" ")
            if before:
                yield {"type": "token", "text": before}

            for raw in match.group(1).split(","):
                number = int(raw.strip())
                # Drop anything outside the supplied range: the model does not get to
                # invent a source.
                if not 1 <= number <= len(docs):
                    continue
                ref = source_ref(docs[number - 1], number)
                if ref["source"] not in seen:
                    seen.add(ref["source"])
                yield {"type": "citation", **ref}

        # Hold back a tail that could still become a marker; release the rest.
        if (match := _PARTIAL.search(buffer)) is not None:
            hold = match.start()
            # Hold the whitespace in front of it too, otherwise a marker arriving in
            # the next chunk has already had its leading space emitted and we get
            # "dispatch ." instead of "dispatch."
            while hold > 0 and buffer[hold - 1] == " ":
                hold -= 1
        else:
            hold = len(buffer)
        if hold > 0:
            yield {"type": "token", "text": buffer[:hold]}
            buffer = buffer[hold:]

        usage = getattr(chunk, "usage_metadata", None)
        if usage:
            yield {"type": "usage", "usage": dict(usage)}

    # Anything left is real text, not a marker.
    if buffer:
        yield {"type": "token", "text": buffer}
