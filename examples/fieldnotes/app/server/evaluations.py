from tenchi.evaluations import create_evaluation_runner, evaluation_group

from app.features.knowledge.evaluations import evaluations as knowledge_evaluations
from app.server.runtime import DATABASE_PATH, create_context, create_lifespan

evaluations = evaluation_group(knowledge_evaluations)

runner = create_evaluation_runner(
    evaluations=evaluations,
    context_factory=create_context,
    lifespan=create_lifespan(DATABASE_PATH),
    concurrency=2,
)
