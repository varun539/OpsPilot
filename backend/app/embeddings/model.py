"""Loading and validation for the pinned CPU-only embedding model.

The model identity and revision are pinned here so every downstream artifact
can be traced to an immutable commit instead of a mutable repository name.
"""

from __future__ import annotations

import torch
from sentence_transformers import SentenceTransformer

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"

#: Dimension produced by ``all-MiniLM-L6-v2``.
EXPECTED_DIMENSION = 384

#: Minimum model input length required by the token-aware chunk budget (240
#: tokens including special tokens) plus headroom. The model's actual
#: ``max_seq_length`` must be at least this large.
MIN_MAX_SEQ_LENGTH = 240


class ModelLoadError(RuntimeError):
    """Raised when the embedding model cannot be loaded or validated."""


def verify_cpu_only() -> None:
    """Raise :class:`ModelLoadError` if PyTorch is not CPU-only."""
    if getattr(torch.version, "cuda", None) is not None:
        raise ModelLoadError(
            "PyTorch CUDA build detected (torch.version.cuda is not None); "
            "CPU-only PyTorch is required for this project."
        )
    try:
        cuda_available = torch.cuda.is_available()
    except Exception as exc:  # pragma: no cover - defensive
        raise ModelLoadError(f"could not query torch.cuda.is_available(): {exc}") from exc
    if cuda_available:
        raise ModelLoadError("torch.cuda.is_available() is True; CPU-only execution required.")


def _tokenizer_revision(tokenizer: object) -> str | None:
    """Recover the immutable revision from a tokenizer, if possible."""
    try:
        from backend.app.ingestion.token_chunker import _tokenizer_revision as recover
    except Exception:  # pragma: no cover - defensive
        recover = None
    if recover is not None:
        try:
            return recover(tokenizer)
        except Exception:  # pragma: no cover - defensive
            return None
    # Fallback: search common attributes.
    for attr in ("_commit_hash", "commit_hash"):
        value = getattr(tokenizer, attr, None)
        if isinstance(value, str) and len(value) == 40:
            return value
    vocab_file = getattr(tokenizer, "vocab_file", None)
    if isinstance(vocab_file, str):
        import re

        match = re.search(r"snapshots/([0-9a-f]{40})", vocab_file)
        if match:
            return match.group(1)
    return None


def load_embedding_model(
    device: str = "cpu",
    local_files_only: bool = False,
    model_id: str = MODEL_ID,
    revision: str = MODEL_REVISION,
    expected_dimension: int = EXPECTED_DIMENSION,
    min_max_seq_length: int = MIN_MAX_SEQ_LENGTH,
) -> SentenceTransformer:
    """Load and validate the pinned SentenceTransformer on CPU.

    Parameters
    ----------
    device:
        Must be ``"cpu"``. GPU devices are rejected.
    local_files_only:
        When ``False`` (default) the model weights may be downloaded from the
        Hugging Face Hub. When ``True`` only the local cache is used.
    """
    if device != "cpu":
        raise ValueError(f"device must be 'cpu', got {device!r}")
    verify_cpu_only()

    try:
        model = SentenceTransformer(
            model_id,
            revision=revision,
            device=device,
            trust_remote_code=False,
            local_files_only=local_files_only,
        )
    except Exception as exc:
        raise ModelLoadError(
            f"failed to load embedding model {model_id!r} (revision {revision}): {exc}"
        ) from exc

    # Validate dimension.
    dimension = None
    for _name in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        _method = getattr(model, _name, None)
        if callable(_method):
            try:
                dimension = _method()
                break
            except Exception:  # pragma: no cover - defensive
                continue
    if dimension is None:
        raise ModelLoadError("could not read model embedding dimension")
    if dimension != expected_dimension:
        raise ModelLoadError(
            f"model embedding dimension {dimension} != expected {expected_dimension}"
        )

    # Validate maximum sequence length.
    max_seq_length = getattr(model, "max_seq_length", None)
    if max_seq_length is None:
        raise ModelLoadError("model does not report max_seq_length")
    if max_seq_length < min_max_seq_length:
        raise ModelLoadError(
            f"model max_seq_length {max_seq_length} is smaller than the required "
            f"{min_max_seq_length}-token budget; refusing to silently truncate."
        )

    # Validate tokenizer identity/revision when available.
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is not None:
        revision_found = _tokenizer_revision(tokenizer)
        if revision_found is not None and revision_found != revision:
            raise ModelLoadError(
                f"tokenizer revision {revision_found} does not match pinned {revision}"
            )

    return model


def model_tokenizer(model: SentenceTransformer) -> object | None:
    """Return the tokenizer attached to ``model`` (if any)."""
    return getattr(model, "tokenizer", None)


def model_tokenizer_revision(model: SentenceTransformer) -> str | None:
    """Return the tokenizer revision recorded by ``model``."""
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None:
        return None
    return _tokenizer_revision(tokenizer)


def model_max_seq_length(model: SentenceTransformer) -> int:
    """Return the model's maximum sequence length."""
    value = getattr(model, "max_seq_length", None)
    if value is None:
        raise ModelLoadError("model does not report max_seq_length")
    return int(value)
