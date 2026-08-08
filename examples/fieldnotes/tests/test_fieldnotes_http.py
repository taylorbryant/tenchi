from pathlib import Path

from app.infra.static_token_directory import DEMO_TOKENS
from app.server.asgi import create_fieldnotes_app
from app.server.worker import drain
from tenchi.testing import open_http


async def test_save_index_search_and_answer_are_owner_scoped(tmp_path: Path) -> None:
    database_path = str(tmp_path / "fieldnotes.db")
    app = create_fieldnotes_app(database_path=database_path)
    alice = {"authorization": "Bearer alice-token"}
    bob = {"authorization": "Bearer bob-token"}

    async with open_http(app) as http:
        unauthorized = await http.get("/sources")
        assert unauthorized.status_code == 401

        saved = await http.post(
            "/sources",
            headers=alice,
            json={
                "title": "MCP security",
                "url": "https://example.com/mcp",
                "content": (
                    "Explicit approval protects destructive MCP tools. "
                    "Authorization remains inside application use cases."
                ),
            },
        )
        assert saved.status_code == 202
        assert saved.json()["status"] == "pending"

        before_indexing = await http.post(
            "/search",
            headers=alice,
            json={"query": "destructive approval"},
        )
        assert before_indexing.json() == {"matches": []}

    assert await drain(database_path) == 1

    async with open_http(app) as http:
        sources = await http.get("/sources", headers=alice)
        assert sources.json()[0]["status"] == "indexed"

        search = await http.post(
            "/search",
            headers=alice,
            json={"query": "destructive approval"},
        )
        assert search.status_code == 200
        assert search.json()["matches"][0]["title"] == "MCP security"

        answer = await http.post(
            "/answers",
            headers=alice,
            json={"question": "What protects destructive tools?"},
        )
        payload = answer.json()
        assert answer.status_code == 200
        assert payload["has_citations"] is True
        assert "grounded" not in payload
        assert "Explicit approval" in payload["text"]
        assert payload["citations"][0]["source_id"] == saved.json()["id"]

        assert (await http.get("/sources", headers=bob)).json() == []
        bob_search = await http.post(
            "/search",
            headers=bob,
            json={"query": "destructive approval"},
        )
        assert bob_search.json() == {"matches": []}

    assert set(DEMO_TOKENS) == {"alice-token", "bob-token"}
