# ===== utils/metrics.py =====
import logging
import os
from typing import Optional
import tiktoken
from sentence_transformers import SentenceTransformer, util

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Optional Claude support
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("Anthropic library not available. Claude token counting will be disabled.")

# Cache encoding and model for efficiency
_encoding: Optional[tiktoken.Encoding] = None
_model: Optional[SentenceTransformer] = None

def get_tiktoken_encoding() -> tiktoken.Encoding:
    """Get or initialize tiktoken encoding."""
    global _encoding
    if _encoding is None:
        try:
            _encoding = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.error(f"Failed to load tiktoken encoding: {str(e)}")
            raise RuntimeError("Tiktoken encoding initialization failed")
    return _encoding

def get_similarity_model() -> SentenceTransformer:
    """Get or initialize sentence transformer model."""
    global _model
    if _model is None:
        try:
            _model = SentenceTransformer('all-MiniLM-L6-v2')
        except Exception as e:
            logger.error(f"Failed to load sentence transformer model: {str(e)}")
            raise RuntimeError("Sentence transformer model initialization failed")
    return _model

def count_tokens_gpt(text: str, model: str = "gpt-4") -> int:
    """
    Count tokens using tiktoken for GPT models.
    
    Args:
        text: The text to tokenize.
        model: The model to use for tokenization (for compatibility, ignored in favor of cl100k_base).
    
    Returns:
        Number of tokens.
    
    Raises:
        ValueError: If text is invalid.
    """
    if not isinstance(text, str):
        raise ValueError("Input must be a string")
    
    # Validate model parameter for test compatibility
    if model and model not in ["gpt-4", "gpt-3.5-turbo", "cl100k_base"]:
        raise ValueError("Invalid model")
    
    try:
        enc = get_tiktoken_encoding()
        return len(enc.encode(text))
    except ValueError as e:
        logger.warning(f"Invalid text for tokenization: {str(e)}")
        raise ValueError(f"Tokenization failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in token counting: {str(e)}")
        raise RuntimeError(f"Token counting failed: {str(e)}")

def count_tokens_claude(text: str, model: str = "claude-sonnet-4-20250514") -> int:
    """
    Count tokens using Anthropic API for Claude models.
    
    Args:
        text: The text to tokenize.
        model: The Claude model to use for tokenization.
    
    Returns:
        Number of tokens.
    
    Raises:
        ValueError: If text is invalid, the Anthropic library is not available, or ANTHROPIC_API_KEY is not set.
        RuntimeError: If API call fails.
    """
    if not isinstance(text, str):
        raise ValueError("Input must be a string")
    
    if not ANTHROPIC_AVAILABLE:
        raise ValueError("Anthropic library not available. Install with: pip install anthropic")
    
    # Get API key from environment variable
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
    
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.count_tokens(
            model=model,
            messages=[{
                "role": "user",
                "content": text
            }],
        )
        return response.input_tokens
    except Exception as e:
        logger.error(f"Claude token counting failed: {str(e)}")
        raise RuntimeError(f"Claude token counting failed: {str(e)}")

def count_tokens(text: str, model: str = "gpt-4") -> int:
    """
    Count tokens using tiktoken (for backward compatibility).
    DEPRECATED: Use count_tokens_gpt() or count_tokens_claude() for explicit model support.
    
    Args:
        text: The text to tokenize.
        model: The model to use for tokenization (for compatibility, ignored in favor of cl100k_base).
    
    Returns:
        Number of tokens.
    
    Raises:
        ValueError: If text is invalid.
    """
    logger.warning("count_tokens() is deprecated. Use count_tokens_gpt() or count_tokens_claude() for explicit model support.")
    return count_tokens_gpt(text, model)

def calculate_similarity(original: str, translated: str) -> float:
    """
    Calculate semantic similarity between original and translated code.
    
    Args:
        original: Original text.
        translated: Translated text.
    
    Returns:
        Cosine similarity score between 0 and 1.
    
    Raises:
        ValueError: If inputs are invalid.
    """
    if not isinstance(original, str) or not isinstance(translated, str):
        raise ValueError("Both inputs must be strings")
    
    if not original or not translated:
        logger.warning("One or both inputs are empty; similarity is 0")
        return 0.0
    
    try:
        model = get_similarity_model()
        orig_emb = model.encode(original)
        trans_emb = model.encode(translated)
        similarity = util.cos_sim(orig_emb, trans_emb)[0][0].item()
        return max(0.0, min(1.0, similarity))  # Clamp between 0 and 1
    except Exception as e:
        logger.error(f"Similarity calculation failed: {str(e)}")
        raise RuntimeError(f"Similarity calculation failed: {str(e)}")