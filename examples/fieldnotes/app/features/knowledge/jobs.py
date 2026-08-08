"""Stable indexing messages owned by the knowledge feature."""

from tenchi.jobs import job

from .schemas import IndexSource

index_source_job = job(
    "knowledge.index_source",
    request=IndexSource,
    result=None,
    description="Split one saved source into searchable passages.",
)
