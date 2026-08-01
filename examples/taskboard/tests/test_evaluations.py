from app.server.evaluations import runner


async def test_project_search_ranking_evaluation_passes() -> None:
    report = await runner.run("projects.search_ranking")

    assert report.ok is True
    assert report.evaluations[0].metrics[0].average == 1.0
