"""Embedding provider using litellm for multi-backend support."""

from typing import Any

import litellm
from loguru import logger


class EmbeddingProvider:
    """Provider for generating text embeddings via litellm.

    Supports multiple backends (ollama, openai, etc.) based on model prefix.
    Default is ollama/nomic-embed-text for local-first operation.
    """

    MODEL_DIMENSIONS: dict[str, int] = {
        # Ollama models
        "ollama/nomic-embed-text": 768,
        "ollama/mxbai-embed-large": 1024,
        "ollama/all-minilm": 384,
        # OpenAI models
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "ollama/nomic-embed-text",
    ):
        self.api_key = api_key
        self.api_base = api_base
        self.default_model = default_model

    @property
    def dimensions(self) -> int:
        """Get the embedding dimension for the current model."""
        return self.MODEL_DIMENSIONS.get(self.default_model, 768)

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        """Generate embedding vector for a single text.

        Args:
            text: The text to embed.
            model: Optional model override.

        Returns:
            List of floats representing the embedding vector.
        """
        model = model or self.default_model

        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "input": [text],
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base

            response = await litellm.aembedding(**kwargs)
            return response.data[0]["embedding"]

        except Exception as e:
            logger.error(f"Embedding failed for model {model}: {e}")
            raise

    async def embed_batch(
        self, texts: list[str], model: str | None = None
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed.
            model: Optional model override.

        Returns:
            List of embedding vectors.
        """
        if not texts:
            return []

        model = model or self.default_model

        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "input": texts,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base

            response = await litellm.aembedding(**kwargs)
            return [d["embedding"] for d in response.data]

        except Exception as e:
            logger.error(f"Batch embedding failed for model {model}: {e}")
            raise

    def get_dimensions(self, model: str | None = None) -> int:
        """Get the embedding dimension for a specific model.

        Args:
            model: Model name. Uses default_model if not specified.

        Returns:
            Dimension size for the model.
        """
        model = model or self.default_model
        return self.MODEL_DIMENSIONS.get(model, 768)
