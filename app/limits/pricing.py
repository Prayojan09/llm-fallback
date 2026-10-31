"""Tokenizer estimates and configurable per-model pricing."""

import math
from pathlib import Path

import tiktoken
import yaml
from pydantic import BaseModel, Field

from app.schemas.chat import ChatCompletionRequest


class ModelPrice(BaseModel):
    """Input and output price in USD per million tokens."""

    input_usd_per_million: float = Field(ge=0)
    output_usd_per_million: float = Field(ge=0)


class PricingCatalog:
    """Load model prices and compute estimates and actual request costs."""

    def __init__(self, models: dict[str, ModelPrice], chars_per_token: int) -> None:
        """Store validated prices and heuristic fallback ratio."""
        self._models = models
        self._chars_per_token = chars_per_token

    @classmethod
    def load(cls, path: str, chars_per_token: int) -> "PricingCatalog":
        """Load a pricing catalog from YAML."""
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        models = {
            name: ModelPrice.model_validate(value) for name, value in raw.get("models", {}).items()
        }
        return cls(models, chars_per_token)

    def estimate_input_tokens(self, request: ChatCompletionRequest, model: str) -> int:
        """Estimate input tokens with tiktoken, falling back to a character heuristic."""
        text = "\n".join(f"{message.role}: {message.content}" for message in request.messages)
        try:
            encoding = tiktoken.encoding_for_model(model)
        except Exception:
            return max(1, math.ceil(len(text) / self._chars_per_token))
        return max(1, len(encoding.encode(text)))

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost for given input and worst-case output tokens."""
        price = self._price(model)
        return (
            input_tokens * price.input_usd_per_million
            + output_tokens * price.output_usd_per_million
        ) / 1_000_000

    def actual_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate provider-reported usage cost at configured rates."""
        return self.estimate_cost(model, input_tokens, output_tokens)

    def _price(self, model: str) -> ModelPrice:
        """Find exact price or reject an unpriced model rather than undercount."""
        try:
            return self._models[model]
        except KeyError as exc:
            raise KeyError(f"No pricing configured for model {model}") from exc
