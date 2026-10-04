"""Records the differential-test coverage reported in docs/research/R41_MEMO.md."""
import collections
import json
import os
import random

from . import diff, run_study_streams
from .bench import OUT
from .instances import random_instance
from .kernel import with_ladder


def main():
    tot = bad = 0
    levels = collections.Counter()
    for seed in (11, 12, 13, 14):
        rng = random.Random(seed)
        for _ in range(1500):
            n = rng.randint(1, 10)
            reps = random_instance(rng, n, n_values=rng.choice([2, 3, 4, 5]), n_origins=rng.choice([1, 2, 3]),
                                   anchor_span=rng.choice([3, 6, 10, 20]), p_change=rng.choice([0, 0.3, 0.6]),
                                   p_from=rng.choice([0, 0.6, 1.0]), p_corr=rng.choice([0, 0.15, 0.35]))
            spec = {}
            x = rng.random()
            if x < 0.06:
                spec = {"error_allowed": False}
            elif x < 0.12:
                spec = {"competing": False}
            pol = rng.choice(["P0", "P0c", "P0cc", "P0cSU"])
            levels[with_ladder(reps, pol, spec.get("error_allowed", True), spec.get("competing", True))[1]] += 1
            tot += 1
            bad += bool(diff.compare(reps, pol, spec))
    study = run_study_streams.run(300)
    out = {"random_instances": tot, "random_mismatches": bad, "random_max_n": 10,
           "ladder_levels": dict(levels), "study": study}
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "differential.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "study"}),
          {k: v for k, v in study.items() if k != "examples"})


if __name__ == "__main__":
    main()
