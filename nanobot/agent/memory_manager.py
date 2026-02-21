"""Memory manager for semantic memory storage and retrieval.

Uses EmbeddingProvider for vector generation and VectorStore for persistence.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import time
from typing import TYPE_CHECKING, Literal

from loguru import logger

from nanobot.providers.vector_store import MemoryMetadata, MemoryRecord

if TYPE_CHECKING:
    from nanobot.providers.embeddings import EmbeddingProvider
    from nanobot.providers.vector_store import VectorStore


class Memory:
    """Semantic memory manager.

    Orchestrates embedding generation and vector storage for memory operations.
    Handles deduplication, categorization, and weight decay.
    """

    # Cooldown to prevent rapid duplicate storage (seconds)
    DEDUP_COOLDOWN = 10.0

    def __init__(
        self,
        embeddings: EmbeddingProvider,
        store: VectorStore,
    ):
        """Initialize memory manager.

        Args:
            embeddings: Provider for generating text embeddings.
            store: Vector store for persistence.
        """
        self.embeddings = embeddings
        self.store = store

        # In-memory cache for deduplication timing
        self._recent_hashes: dict[str, float] = {}

    def _is_recent_duplicate(self, content_hash: str) -> bool:
        """Check if content was recently stored (timing-based dedup).

        Args:
            content_hash: Hash of the content.

        Returns:
            True if duplicate within cooldown period.
        """
        now = time()

        if content_hash in self._recent_hashes:
            last_time = self._recent_hashes[content_hash]
            if now - last_time < self.DEDUP_COOLDOWN:
                return True

        self._recent_hashes[content_hash] = now
        return False

    async def add(
        self,
        content: str,
        author: str,
        tag: Literal["core", "crucial", "default"] = "default",
        categories: list[str] | None = None,
        source: str | None = None,
        project: str | None = None,
        decision: bool = False,
        weight: float = 0.5,
    ) -> MemoryRecord | None:
        """Add a memory.

        Args:
            content: Text content to store.
            author: Who/what created this memory.
            tag: Decay tier (core/crucial/default).
            categories: Semantic categories.
            source: Origin (conversation, reflection, observation, cron).
            project: Optional project scope.
            decision: Whether this is a decision memory for reflection.
            weight: Initial salience (0.0-1.0).

        Returns:
            MemoryRecord if stored, None if rejected (empty/duplicate).
        """
        # Validate content
        if not content or not content.strip():
            logger.debug("Rejected empty memory content")
            return None

        # Generate content hash for deduplication
        content_hash = MemoryRecord.content_hash(content)

        # Check for rapid duplicates (timing)
        if self._is_recent_duplicate(content_hash):
            logger.debug("Rejected duplicate memory within cooldown")
            return None

        # Check for existing memory with same hash (persistent dedup)
        existing = self.store.get_by_hash(content_hash)
        if existing:
            # Bump weight of existing memory instead of creating duplicate
            new_weight = min(1.0, existing.metadata.weight + 0.1)
            self.store.update_weight(existing.id, new_weight)
            logger.debug(f"Bumped weight of existing memory {existing.id[:16]}...")
            return existing

        # Generate embedding
        try:
            embedding = await self.embeddings.embed(content)
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            return None

        # Create memory record
        memory_id = MemoryRecord.generate_id(content, project)
        metadata = MemoryMetadata(
            tag=tag,
            weight=weight,
            timestamp=datetime.now().isoformat(),
            author=author,
            categories=categories or [],
            content_hash=content_hash,
            source=source,
            project=project,
            decision=decision,
        )
        memory = MemoryRecord(id=memory_id, content=content, metadata=metadata)

        # Store
        self.store.insert(memory, embedding)

        logger.debug(
            f"Stored memory {memory_id[:16]}... "
            f"[tag={tag}, author={author}, project={project}]"
        )
        return memory

    async def search(
        self,
        query: str,
        limit: int = 5,
        project: str | None = None,
        categories: list[str] | None = None,
        min_weight: float = 0.0,
    ) -> list[MemoryRecord]:
        """Search for similar memories.

        Args:
            query: Search query text.
            limit: Maximum results to return.
            project: Optional project filter.
            categories: Optional category filter (matches any).
            min_weight: Minimum weight threshold.

        Returns:
            List of matching MemoryRecords with similarity scores.
        """
        try:
            query_embedding = await self.embeddings.embed(query)
        except Exception as e:
            logger.error(f"Failed to generate query embedding: {e}")
            return []

        return self.store.search(
            query_embedding=query_embedding,
            limit=limit,
            project=project,
            categories=categories,
            min_weight=min_weight,
        )

    def get(self, memory_id: str) -> MemoryRecord | None:
        """Get a specific memory by ID."""
        return self.store.get(memory_id)

    def delete(self, memory_id: str) -> bool:
        """Delete a memory."""
        return self.store.delete(memory_id)

    def list_decisions(self, limit: int = 50) -> list[MemoryRecord]:
        """List decision memories for reflection."""
        return self.store.list_decisions(limit)

    def list_by_project(self, project: str) -> list[MemoryRecord]:
        """List memories for a project."""
        return self.store.list_by_project(project)

    def bump_weight(self, memory_id: str, amount: float = 0.1) -> bool:
        """Increase a memory's weight (reinforcement on recall)."""
        memory = self.store.get(memory_id)
        if not memory:
            return False

        new_weight = min(1.0, memory.metadata.weight + amount)
        return self.store.update_weight(memory_id, new_weight)

    def decay_all(self) -> dict[str, int]:
        """Apply tag-based decay to all memory weights.

        Returns:
            Dict mapping tag to count of memories decayed.
        """
        results = self.store.decay_weights()
        total = sum(results.values())
        if total > 0:
            logger.debug(f"Decayed weights: {results}")
        return results

    def count(self, project: str | None = None, tag: str | None = None) -> int:
        """Count memories with optional filters."""
        return self.store.count(project=project, tag=tag)


def create_memory_manager(
    workspace: Path,
    embedding_model: str = "ollama/nomic-embed-text",
    api_key: str | None = None,
    api_base: str | None = None,
) -> Memory:
    """Factory function to create a Memory manager with default providers.

    Args:
        workspace: Workspace directory (database will be at workspace/memory.db).
        embedding_model: Model for embeddings.
        api_key: Optional API key for embedding provider.
        api_base: Optional API base URL.

    Returns:
        Configured Memory instance.
    """
    from nanobot.providers.embeddings import EmbeddingProvider
    from nanobot.providers.vector_store import VectorStore

    embeddings = EmbeddingProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=embedding_model,
    )

    store = VectorStore(
        db_path=workspace / "memory.db",
        dimensions=embeddings.dimensions,
    )
    store.connect()

    return Memory(embeddings=embeddings, store=store)
