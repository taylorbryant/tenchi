from app.features.projects.evaluations import evaluations as project_evaluations
from app.server.runtime import DATABASE_PATH, create_context, create_lifespan
from tenchi.evaluations import create_evaluation_runner, evaluation_group

evaluations = evaluation_group(project_evaluations)

runner = create_evaluation_runner(
    evaluations=evaluations,
    context_factory=create_context,
    lifespan=create_lifespan(DATABASE_PATH),
    concurrency=2,
)
