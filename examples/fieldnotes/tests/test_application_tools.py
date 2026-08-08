import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from app.features.knowledge.schemas import (
    AskQuestion,
    GeneratedAnswer,
    Passage,
    PassageMatch,
    SaveSource,
    SearchKnowledge,
)
from app.infra.memory_knowledge import (
    MemoryOutbox,
    MemoryPassageIndex,
    MemorySourceRepository,
)
from app.infra.static_token_directory import StaticTokenDirectory
from app.server.context import AppContext
from app.server.mcp import create_fieldnotes_mcp_server
from app.server.tools import create_user_tool_runner, tools
from app.server.worker import drain
from app.shared.errors import answer_generation_timed_out
from app.shared.users import User
from httpx2 import ASGITransport, AsyncClient
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from tenchi.errors import AppError
from tenchi.tools import create_tool_runner, tool_manifest

ALICE = User(id="alice", name="Alice")


def test_tool_manifest_exposes_ai_safety_and_schema_metadata() -> None:
    manifest = tool_manifest(tools)

    assert [item["name"] for item in manifest["tools"]] == [
        "knowledge.answer",
        "knowledge.search",
        "sources.save",
    ]
    answer, search, save = manifest["tools"]
    assert answer["annotations"]["read_only"] is True
    assert answer["annotations"]["open_world"] is True
    assert search["annotations"]["open_world"] is False
    assert save["annotations"]["destructive"] is True
    assert save["annotations"]["idempotent"] is False


async def test_tools_reuse_ingestion_retrieval_and_answering(tmp_path: Path) -> None:
    database_path = str(tmp_path / "fieldnotes.db")
    runner = create_user_tool_runner(database_path=database_path, user=ALICE)

    source = await runner.call(
        "sources.save",
        input=SaveSource(
            title="Evaluation gates",
            content="Evaluation thresholds prevent silent quality regressions.",
        ),
    )
    assert source.status == "pending"
    assert await drain(database_path) == 1

    search = await runner.call(
        "knowledge.search",
        input=SearchKnowledge(query="quality thresholds"),
    )
    assert search.matches[0].source_id == source.id

    answer = await runner.call(
        "knowledge.answer",
        input=AskQuestion(question="What prevents quality regressions?"),
    )
    assert answer.has_citations is True
    assert answer.citations[0].source_id == source.id


class HangingAnswerGenerator:
    async def generate(
        self,
        *,
        question: str,
        passages: Sequence[PassageMatch | Passage],
    ) -> GeneratedAnswer:
        del question, passages
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_answer_tool_bounds_provider_calls() -> None:
    context = AppContext(
        sources=MemorySourceRepository(),
        passages=MemoryPassageIndex(),
        answer_generator=HangingAnswerGenerator(),
        outbox=MemoryOutbox(),
        answer_timeout_seconds=0.01,
        user=ALICE,
    )
    runner = create_tool_runner(
        tools=tools,
        context_factory=lambda: context,
    )

    with pytest.raises(AppError) as captured:
        await runner.call(
            "knowledge.answer",
            input=AskQuestion(question="Will this provider finish?"),
        )

    assert captured.value.definition == answer_generation_timed_out


async def test_mcp_requires_approval_before_saving_sources(tmp_path: Path) -> None:
    database_path = str(tmp_path / "fieldnotes.db")
    directory = StaticTokenDirectory({"alice-token": ALICE})
    server = create_fieldnotes_mcp_server(
        database_path=database_path,
        token_directory=directory,
    )

    async with Client(server) as session:
        with pytest.raises(MCPError, match="authentication failed"):
            await session.list_tools()

    mcp_app = server.streamable_http_app()
    transport = ASGITransport(app=mcp_app)
    async with (
        mcp_app.router.lifespan_context(mcp_app),
        AsyncClient(
            transport=transport,
            base_url="http://localhost:8001",
            headers={"authorization": "Bearer alice-token"},
        ) as http,
        Client(
            streamable_http_client(
                "http://localhost:8001/mcp",
                http_client=http,
            )
        ) as session,
    ):
        listed = await session.list_tools()
        denied = await session.call_tool(
            "sources.save",
            {"title": "Private note", "content": "Do not save without approval."},
        )

    assert [item.name for item in listed.tools] == [
        "knowledge.answer",
        "knowledge.search",
        "sources.save",
    ]
    assert denied.is_error is True
    assert denied.structured_content is not None
    assert denied.structured_content["error"]["kind"] == "approval_required"
    assert not Path(database_path).exists()

    approved = create_fieldnotes_mcp_server(
        database_path=database_path,
        token_directory=directory,
        approve=lambda principal, declaration, input_value: True,
    )
    approved_app = approved.streamable_http_app()
    approved_transport = ASGITransport(app=approved_app)
    async with (
        approved_app.router.lifespan_context(approved_app),
        AsyncClient(
            transport=approved_transport,
            base_url="http://localhost:8001",
            headers={"authorization": "Bearer alice-token"},
        ) as http,
        Client(
            streamable_http_client(
                "http://localhost:8001/mcp",
                http_client=http,
            )
        ) as session,
    ):
        saved = await session.call_tool(
            "sources.save",
            {"title": "Approved note", "content": "Approval was explicit."},
        )

    assert saved.structured_content is not None
    assert saved.structured_content["ok"] is True
    assert saved.structured_content["result"]["status"] == "pending"
