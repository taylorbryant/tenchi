from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl, model_validator

JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991


class SourceStatus(StrEnum):
    PENDING = "pending"
    INDEXED = "indexed"


class SaveSource(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)
    url: HttpUrl | None = None


class Source(BaseModel):
    id: str
    title: str
    url: HttpUrl | None = None
    status: SourceStatus


class StoredSource(BaseModel):
    id: str
    owner_id: str
    title: str
    content: str
    url: HttpUrl | None = None
    status: SourceStatus


class IndexSource(BaseModel):
    source_id: str


class SearchKnowledge(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class Passage(BaseModel):
    id: str
    source_id: str
    title: str
    text: str
    position: int
    url: HttpUrl | None = None


class PassageMatch(Passage):
    score: float = Field(ge=0, le=1)


class SearchResults(BaseModel):
    matches: tuple[PassageMatch, ...]


class AskQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=5, ge=1, le=20)


class GeneratedAnswer(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    cited_passage_ids: tuple[str, ...] = ()
    tokens: int | None = Field(default=None, ge=0, le=JSON_SAFE_INTEGER_MAX)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Citation(BaseModel):
    passage_id: str
    source_id: str
    title: str
    url: HttpUrl | None = None


class Answer(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    citations: tuple[Citation, ...]
    has_citations: bool
    tokens: int | None = Field(default=None, ge=0, le=JSON_SAFE_INTEGER_MAX)


class ReindexSourcesInput(BaseModel):
    dry_run: bool = True


class ReindexSourcesResult(BaseModel):
    discovered: int
    enqueued: int
    dry_run: bool


class RankingCandidate(BaseModel):
    id: str
    title: str
    text: str


class RankingCase(BaseModel, frozen=True):
    query: str
    candidates: tuple[RankingCandidate, ...]
    expected_first: str


class AnswerCase(BaseModel, frozen=True):
    question: str
    passages: tuple[Passage, ...]
    required_text: str

    @model_validator(mode="after")
    def require_passages(self) -> "AnswerCase":
        if not self.passages:
            raise ValueError("answer evaluation cases require at least one passage")
        return self
