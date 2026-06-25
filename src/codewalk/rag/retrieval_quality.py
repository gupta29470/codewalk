"""Retrieval Quality utilities for Codewalk."""
from src.codewalk.log import log as _log

HARD_CUTOFF = 0.75   # above this = definitely noise
SOFT_CUTOFF = 0.55   # above this = questionable relevance; lowered to improve recall
MIN_GOOD_CHUNKS = 1  # need at least this many good chunks; lowered for large codebases

def filter_by_distance(results: list[dict]) -> tuple[list[dict], float]:
    """Filter retrieved chunks by cosine distance. Returns (filtered, confidence).

    Args:
        results: Search results from store.search(). Each has a "distance" key.

    Returns:
        (filtered_results, confidence_score)
        - filtered_results: chunks that passed the distance filter
        - confidence_score: 0.0 to 1.0 indicating retrieval quality
          1.0 = all chunks are strong matches
          0.0 = all chunks are noise
    """
    if not results:
        return [], 0.0
    
    filtered = []
    good_count = 0  # chunks below SOFT_CUTOFF

    for result in results:
        distance = result.get("distance")

        if distance is None:
            filtered.append(result)
            continue

        if distance > HARD_CUTOFF:
            _log(f"[quality] DROPPED distance={distance:.3f} — {result['metadata'].get('file_path', '?')}")
            continue

        if distance <= SOFT_CUTOFF:
            good_count += 1

        filtered.append(result)
    
    total = len(results)
    confidence = (good_count / total) if total > 0 else 0.0

    _log(f"[quality] {len(filtered)}/{total} chunks kept, "
         f"{good_count} good (< {SOFT_CUTOFF}), confidence={confidence:.2f}")
    
    return filtered, confidence

def is_retreival_good(confidence: float, filtered_count: int) -> bool:
    """Decide if retrieval quality is good enough to proceed to generation.

    Args:
        confidence: Score from filter_by_distance() (0.0-1.0).
        filtered_count: Number of chunks that passed the filter.

    Returns:
        True if we should generate an answer from these chunks.
        False if we should rewrite the query and retry.
    """
    if filtered_count < MIN_GOOD_CHUNKS:
        return False
    if confidence < 0.25:
        return False
    
    return True