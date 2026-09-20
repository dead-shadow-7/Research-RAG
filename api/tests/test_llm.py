"""Generation-layer tests.

The provider is OpenAI-compatible, so citations are prompt-driven `[n]` markers rather
than verified spans. These tests pin the two behaviours that keep that honest: markers
never survive into visible text, and a marker can never resolve to a source that was not
retrieved.
"""

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

from app import llm as llm_module
from app.llm import build_messages, format_sources, stream_answer


def doc(ordinal, title='handbook.pdf', page=3, text='Bearings carry a 36 month warranty.'):
    return Document(
        page_content=text,
        metadata={
            'document_id': '1111',
            'vector_id': f'1111#{ordinal}',
            'title': title,
            'page_from': page,
            'ordinal': ordinal,
        },
    )


DOCS = [doc(2), doc(3, page=4, text='The warranty is void above 2400 rpm.')]


class _FakeLLM:
    def __init__(self, pieces):
        self._pieces = pieces

    async def astream(self, messages):
        for p in self._pieces:
            yield AIMessageChunk(content=p)


async def collect(pieces, docs=DOCS):
    return [e async for e in stream_answer('q', docs)]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, request):
    pieces = getattr(request, 'param', None)
    if pieces is not None:
        monkeypatch.setattr(llm_module, 'get_llm', lambda: _FakeLLM(pieces))


def use(pieces):
    return pytest.mark.parametrize('_no_network', [pieces], indirect=True)


def test_sources_are_numbered_from_one():
    text = format_sources(DOCS)
    assert text.startswith('[1] handbook.pdf, page 3')
    assert '[2] handbook.pdf, page 4' in text


def test_prompt_carries_sources_and_question():
    messages = build_messages('how long?', DOCS)
    assert isinstance(messages[0], SystemMessage)
    last = messages[-1]
    assert isinstance(last, HumanMessage)
    assert '[1]' in last.content and 'Question: how long?' in last.content


@use(['The warranty runs 36 months [1].'])
@pytest.mark.asyncio
async def test_marker_is_stripped_and_emitted_as_a_citation():
    events = await collect(None)
    text = ''.join(e['text'] for e in events if e['type'] == 'token')
    assert '[1]' not in text, 'markers must not reach the visible answer'
    # The space the model put before the marker goes with it, so no " ." is left behind.
    assert text == 'The warranty runs 36 months.'

    cites = [e for e in events if e['type'] == 'citation']
    assert len(cites) == 1
    assert cites[0]['source'] == '1111#2'
    assert cites[0]['number'] == 1
    # No verified span is available from this provider, so none is claimed.
    assert 'cited_text' not in cites[0]


@use(['The warranty runs 36 months [', '1', '] from dispatch.'])
@pytest.mark.asyncio
async def test_marker_split_across_chunks_does_not_leak():
    events = await collect(None)
    text = ''.join(e['text'] for e in events if e['type'] == 'token')
    assert '[' not in text and '1]' not in text
    assert text == 'The warranty runs 36 months from dispatch.'
    assert [e['number'] for e in events if e['type'] == 'citation'] == [1]


@use(['Both rules apply [1,2].'])
@pytest.mark.asyncio
async def test_grouped_markers_expand_to_several_citations():
    events = await collect(None)
    assert [e['number'] for e in events if e['type'] == 'citation'] == [1, 2]


@use(['Invented support [7].'])
@pytest.mark.asyncio
async def test_out_of_range_marker_is_dropped():
    """The model does not get to cite a document that was never retrieved."""
    events = await collect(None)
    assert [e for e in events if e['type'] == 'citation'] == []
    text = ''.join(e['text'] for e in events if e['type'] == 'token')
    assert '7' not in text


@use(['Plain answer with no citations at all.'])
@pytest.mark.asyncio
async def test_uncited_answer_still_streams():
    events = await collect(None)
    text = ''.join(e['text'] for e in events if e['type'] == 'token')
    assert text == 'Plain answer with no citations at all.'
