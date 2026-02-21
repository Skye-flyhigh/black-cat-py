"""Vector store provider using sqlite-vec for local persistence."""

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import sqlite_vec
from loguru import logger

# Decay rates by tag tier
DECAY_RATES: dict[str, float] = {
    "core": 0.0,       # Never decays (identity, fundamental values)
    "crucial": 0.01,   # Slow decay (important relationships, key facts)
    "default": 0.05,   # Normal decay
}


@dataclass
class MemoryMetadata:
    """Metadata for a memory entry."""

    tag: Literal["core", "crucial", "default"]  # Controls decay rate
    weight: float                # 0.0–1.0, current salience
    timestamp: str               # ISO format, creation time
    author: str                  # Who created this (user, system, reflection, classifier)
    categories: list[str]        # Semantic categories
    content_hash: str            # Dedup key
    source: str | None = None    # Origin: conversation, reflection, observation, cron
    project: str | None = None   # Linked project name for scoped retrieval
    decision: bool = False       # If True, this is a meta:decision memory (feeds reflection)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryMetadata":
        """Create from dictionary."""
        return cls(**data)


@dataclass
class MemoryRecord:
    """A complete memory record with content and metadata."""

    id: str
    content: str
    metadata: MemoryMetadata
    distance: float | None = None  # Populated by search results

    @staticmethod
    def generate_id(content: str, prefix: str | None = None) -> str:
        """Generate a unique ID for content."""
        content_hash = sha256(content.encode()).hexdigest()[:16]
        timestamp = int(datetime.now().timestamp() * 1000)
        if prefix:
            return f"{prefix}_{content_hash}_{timestamp}"
        return f"{content_hash}_{timestamp}"

    @staticmethod
    def content_hash(content: str) -> str:
        """Generate hash for deduplication."""
        return sha256(content.lower().strip().encode()).hexdigest()


class VectorStore:
    """SQLite-vec based vector storage.

    Provides vector similarity search with rich metadata.
    Uses sqlite-vec extension for efficient vector operations.
    """

    def __init__(self, db_path: Path, dimensions: int = 768):
        """Initialize the vector store.

        Args:
            db_path: Path to the SQLite database file.
            dimensions: Embedding vector dimensions (must match your embedding model).
        """
        self.db_path = db_path
        self.dimensions = dimensions
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Initialize database connection and load sqlite-vec extension."""
        if self._conn is not None:
            return

        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)

        self._create_tables()
        logger.debug(f"VectorStore connected to {self.db_path}")

    def _create_tables(self) -> None:
        """Create memories and vector tables if they don't exist."""
        if self._conn is None:
            raise RuntimeError("Database not connected")

        # Main memories table - maps to MemoryMetadata
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                tag TEXT NOT NULL DEFAULT 'default',
                weight REAL NOT NULL DEFAULT 0.5,
                timestamp TEXT NOT NULL,
                author TEXT NOT NULL,
                categories JSON NOT NULL DEFAULT '[]',
                content_hash TEXT NOT NULL,
                source TEXT,
                project TEXT,
                decision INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            )
        """)

        # Index for common queries
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_tag ON memories(tag)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_hash ON memories(content_hash)"
        )

        # Vector table for similarity search
        self._conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors USING vec0(
                id TEXT PRIMARY KEY,
                embedding float[{self.dimensions}]
            )
        """)

        self._conn.commit()

    def insert(self, memory: MemoryRecord, embedding: list[float]) -> None:
        """Insert a memory with its embedding vector.

        Args:
            memory: The memory record to store.
            embedding: Vector embedding of the content.
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")

        meta = memory.metadata

        # Insert or replace metadata
        self._conn.execute(
            """
            INSERT OR REPLACE INTO memories
            (id, content, tag, weight, timestamp, author, categories,
             content_hash, source, project, decision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.id,
                memory.content,
                meta.tag,
                meta.weight,
                meta.timestamp,
                meta.author,
                json.dumps(meta.categories),
                meta.content_hash,
                meta.source,
                meta.project,
                1 if meta.decision else 0,
            ),
        )

        # Insert or replace vector
        self._conn.execute(
            "INSERT OR REPLACE INTO memory_vectors (id, embedding) VALUES (?, ?)",
            (memory.id, embedding),
        )

        self._conn.commit()

    def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        project: str | None = None,
        categories: list[str] | None = None,
        min_weight: float = 0.0,
        include_decisions: bool = False,
    ) -> list[MemoryRecord]:
        """Find similar memories by vector similarity.

        Args:
            query_embedding: Query vector to search against.
            limit: Maximum number of results.
            project: Optional project filter.
            categories: Optional category filter (matches any).
            min_weight: Minimum weight threshold.
            include_decisions: Whether to include decision memories.

        Returns:
            List of matching MemoryRecords with distance scores.
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")

        # Build query with optional filters
        query = """
            SELECT m.*, v.distance
            FROM memory_vectors v
            JOIN memories m ON v.id = m.id
            WHERE v.embedding MATCH ?
              AND k = ?
        """
        params: list[Any] = [query_embedding, limit * 2]  # Fetch extra for filtering

        if project:
            query += " AND m.project = ?"
            params.append(project)

        if min_weight > 0:
            query += " AND m.weight >= ?"
            params.append(min_weight)

        if not include_decisions:
            query += " AND m.decision = 0"

        query += " ORDER BY v.distance LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(query, params)
        results = []

        for row in cursor:
            row_dict = dict(row)

            # Filter by categories if specified (done in Python for JSON array)
            if categories:
                row_categories = json.loads(row_dict["categories"])
                if not any(c in row_categories for c in categories):
                    continue

            memory = self._row_to_memory(row_dict)
            results.append(memory)

        return results[:limit]

    def get(self, id: str) -> MemoryRecord | None:
        """Get a memory by ID.

        Args:
            id: Memory ID to retrieve.

        Returns:
            MemoryRecord or None if not found.
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute("SELECT * FROM memories WHERE id = ?", (id,))
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_memory(dict(row))

    def get_by_hash(self, content_hash: str) -> MemoryRecord | None:
        """Get a memory by content hash (for deduplication).

        Args:
            content_hash: Content hash to look up.

        Returns:
            MemoryRecord or None if not found.
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute(
            "SELECT * FROM memories WHERE content_hash = ?", (content_hash,)
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_memory(dict(row))

    def _row_to_memory(self, row: dict[str, Any]) -> MemoryRecord:
        """Convert a database row to a MemoryRecord."""
        metadata = MemoryMetadata(
            tag=row["tag"],
            weight=row["weight"],
            timestamp=row["timestamp"],
            author=row["author"],
            categories=json.loads(row["categories"]),
            content_hash=row["content_hash"],
            source=row.get("source"),
            project=row.get("project"),
            decision=bool(row["decision"]),
        )

        return MemoryRecord(
            id=row["id"],
            content=row["content"],
            metadata=metadata,
            distance=row.get("distance"),
        )

    def update_weight(self, id: str, weight: float) -> bool:
        """Update the weight of a memory.

        Args:
            id: Memory ID to update.
            weight: New weight value.

        Returns:
            True if updated, False if not found.
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute(
            "UPDATE memories SET weight = ?, updated_at = ? WHERE id = ?",
            (weight, datetime.now().isoformat(), id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def decay_weights(self) -> dict[str, int]:
        """Apply tag-based decay to all memory weights.

        Uses DECAY_RATES to apply different rates based on tag tier.

        Returns:
            Dict mapping tag to count of memories decayed.
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")

        results = {}
        now = datetime.now().isoformat()

        for tag, rate in DECAY_RATES.items():
            if rate == 0:
                results[tag] = 0
                continue

            cursor = self._conn.execute(
                """
                UPDATE memories
                SET weight = MAX(0.1, weight - ?),
                    updated_at = ?
                WHERE tag = ? AND weight > 0.1
                """,
                (rate, now, tag),
            )
            results[tag] = cursor.rowcount

        self._conn.commit()
        return results

    def delete(self, id: str) -> bool:
        """Delete a memory by ID.

        Args:
            id: Memory ID to delete.

        Returns:
            True if deleted, False if not found.
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute("DELETE FROM memories WHERE id = ?", (id,))
        self._conn.execute("DELETE FROM memory_vectors WHERE id = ?", (id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def list_decisions(self, limit: int = 50) -> list[MemoryRecord]:
        """List decision memories for reflection.

        Args:
            limit: Maximum number to return.

        Returns:
            List of decision memories, newest first.
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute(
            "SELECT * FROM memories WHERE decision = 1 ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )

        return [self._row_to_memory(dict(row)) for row in cursor]

    def list_by_project(self, project: str) -> list[MemoryRecord]:
        """List memories for a specific project.

        Args:
            project: Project name to filter by.

        Returns:
            List of memories for the project.
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute(
            "SELECT * FROM memories WHERE project = ? ORDER BY timestamp DESC",
            (project,),
        )

        return [self._row_to_memory(dict(row)) for row in cursor]

    def count(self, project: str | None = None, tag: str | None = None) -> int:
        """Count memories with optional filters.

        Args:
            project: Optional project filter.
            tag: Optional tag filter.

        Returns:
            Number of memories.
        """
        if self._conn is None:
            raise RuntimeError("Database not connected")

        query = "SELECT COUNT(*) FROM memories WHERE 1=1"
        params: list[Any] = []

        if project:
            query += " AND project = ?"
            params.append(project)

        if tag:
            query += " AND tag = ?"
            params.append(tag)

        cursor = self._conn.execute(query, params)
        return cursor.fetchone()[0]

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.debug("VectorStore connection closed")

    def __enter__(self) -> "VectorStore":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
