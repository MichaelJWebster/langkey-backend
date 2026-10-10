"""Weighted sampling without replacement.

Shared by the speaking session and the repetition deck: both want the items
the learner is struggling with to come round more often, and both must never
show the same thing twice in one sitting however strongly it is favoured
(ADR-038).
"""

import random


def weighted_sample(weights: list[float], k: int) -> list[int]:
    """Pick up to `k` distinct indices, each with probability in proportion to
    its weight among those still unpicked."""
    pool = list(range(len(weights)))
    picked: list[int] = []
    for _ in range(min(k, len(pool))):
        total = sum(weights[i] for i in pool)
        if total <= 0:
            picked.extend(pool[:k - len(picked)])
            break
        cut = random.uniform(0, total)
        running = 0.0
        for i in pool:
            running += weights[i]
            if running >= cut:
                picked.append(i)
                pool.remove(i)
                break
    return picked
