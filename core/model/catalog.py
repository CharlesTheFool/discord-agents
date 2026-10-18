"""
Model catalog: one ModelSpec per model family, replacing the substring
tables that used to live in internal_constants (effort markers, output
ceilings, prices).

A spec's marker is matched as a substring of the model id and the FIRST
match wins, so the catalog runs most specific to least: "opus-4-5" before
"opus-4" before "opus". Priced entries come before the bare family entries,
which only carry an output ceiling. Unknown ids get DEFAULT_SPEC: a
conservative ceiling, no effort, no price.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

WIRE_APIS = ("anthropic_messages", "openai_responses")
CACHE_SEMANTICS = ("explicit_breakpoints", "implicit", "none")


@dataclass(frozen=True)
class ModelSpec:
    marker: str                                  # substring of the model id
    wire_api: str = "anthropic_messages"
    cache_semantics: str = "explicit_breakpoints"
    # Output-token ceiling: max_tokens is min(context_tokens, this). Safe
    # floors - the API 400s above the real cap - raise only on documented
    # numbers.
    max_output: int = 16000
    batch_prices: Optional[Tuple[float, float]] = None   # $/MTok in, out
    effort: bool = False            # accepts output_config.effort (else 400)
    thinking: bool = True           # accepts adaptive thinking
    images: bool = True
    pdf: bool = True
    structured_output: bool = True
    server_web_tools: bool = True   # server-side web search and fetch
    files: bool = True
    batches: bool = True
    typed_tools: bool = True        # memory_20250818 / text_editor_20250728

    @property
    def live_prices(self) -> Optional[Tuple[float, float]]:
        """Live $/MTok (input, output): batch is half price."""
        if self.batch_prices is None:
            return None
        batch_in, batch_out = self.batch_prices
        return (batch_in * 2, batch_out * 2)


_SONNET_PRICES = (1.50, 7.50)
_OPUS_PRICES = (2.50, 12.50)   # opus-4-5 onward, repriced below the 4.0/4.1 tier

CATALOG = (
    ModelSpec("fable", max_output=32000, batch_prices=(5.00, 25.00), effort=True),
    ModelSpec("haiku-4-5", batch_prices=(0.50, 2.50), thinking=False),
    ModelSpec("sonnet-4-5", max_output=32000, batch_prices=_SONNET_PRICES),
    ModelSpec("sonnet-4-6", max_output=32000, batch_prices=_SONNET_PRICES, effort=True),
    ModelSpec("sonnet-5", max_output=32000, batch_prices=_SONNET_PRICES, effort=True),
    ModelSpec("opus-4-5", max_output=32000, batch_prices=_OPUS_PRICES, effort=True),
    ModelSpec("opus-4-6", max_output=32000, batch_prices=_OPUS_PRICES, effort=True),
    ModelSpec("opus-4-7", max_output=32000, batch_prices=_OPUS_PRICES, effort=True),
    ModelSpec("opus-4-8", max_output=32000, batch_prices=_OPUS_PRICES, effort=True),
    ModelSpec("opus-4", max_output=32000, batch_prices=(7.50, 37.50)),
    # Families without a priced entry above: ceilings only
    ModelSpec("opus", max_output=32000),
    ModelSpec("sonnet", max_output=32000),
    ModelSpec("haiku", thinking=False),
)

DEFAULT_SPEC = ModelSpec(marker="")


def spec_for(model: Optional[str]) -> ModelSpec:
    """The first catalog entry whose marker appears in the model id."""
    model = model or ""
    return next((spec for spec in CATALOG if spec.marker in model), DEFAULT_SPEC)
