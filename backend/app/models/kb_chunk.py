import uuid
from datetime import datetime
from typing import Final

from pgvector.sqlalchemy import Vector
from sqlalchemy import CHAR, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Width of the embedding column. The embeddings client requests vectors of exactly this
# size, and settings refuse any other value at startup.
EMBEDDING_DIMENSIONS: Final = 1536
DOCUMENT_MAX_LENGTH: Final = 100
TITLE_MAX_LENGTH: Final = 200
HEADING_PATH_MAX_LENGTH: Final = 300
EMBEDDING_MODEL_MAX_LENGTH: Final = 100
CONTENT_HASH_LENGTH: Final = 64
# pgvector's defaults. With a few hundred rows an exact scan would do as well; the index
# keeps queries fast as the knowledge base grows.
HNSW_M: Final = 16
HNSW_EF_CONSTRUCTION: Final = 64


class KbChunk(Base):
    """One retrievable piece of a knowledge base document.

    `content_hash` covers the embedding model and dimensions, the document, the heading
    path, and the embedded text, so an unchanged chunk is recognized and never embedded
    twice, and a change to any of those produces a new row.
    """

    __tablename__ = "kb_chunks"
    __table_args__ = (
        Index(
            "ix_kb_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": HNSW_M, "ef_construction": HNSW_EF_CONSTRUCTION},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_kb_chunks_document", "document"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    # File name without the extension, e.g. "peak-hours".
    document: Mapped[str] = mapped_column(String(DOCUMENT_MAX_LENGTH))
    title: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH))
    # The H2 heading, or the H2 and H3 headings joined by a separator.
    heading_path: Mapped[str] = mapped_column(String(HEADING_PATH_MAX_LENGTH))
    # Position within the document, kept current when sections move.
    chunk_index: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int]
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str] = mapped_column(String(EMBEDDING_MODEL_MAX_LENGTH))
    content_hash: Mapped[str] = mapped_column(CHAR(CONTENT_HASH_LENGTH), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
