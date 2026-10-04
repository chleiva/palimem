"""Differential test on REVISE-STREAM generator output (the study's own streams)."""
from __future__ import annotations

import collections
import json
import sys

from . import oracle_bridge as ob
from . import streams as st


def run(n_streams: int = 120, seed0: int = 0) -> dict:
    ob._load()
    from revise_stream.generator import GenConfig, generate
    from revise_stream.model import stream_from_dict
    hist = collections.Counter()
    total = bad = 0
    examples = []
    for seed in range(seed0, seed0 + n_streams):
        cfg = GenConfig()
        d, _ = generate(seed, cfg, stream_id=f"x{seed}")
        s = stream_from_dict(d)
        for tau in (cfg.T // 3, 2 * cfg.T // 3, cfg.T + 25):
            for key, obs in s.admitted_by_key(tau).items():
                sp = s.attributes[key[1]]
                if sp.derived or sp.cardinality != "single" or not sp.changeable:
                    continue
                hist[len(obs)] += 1
                for pol in ("P0c", "P0cSU"):
                    b = st.compare_stream_key(s, tau, key, pol)
                    total += 1
                    if b:
                        bad += 1
                        if len(examples) < 5:
                            examples.append({"seed": seed, "tau": tau, "key": list(key), "policy": pol, "diff": b[:3]})
    return {"streams": n_streams, "key_instances_compared": total, "mismatches": bad,
            "n_histogram": dict(sorted(hist.items())), "examples": examples}


if __name__ == "__main__":
    print(json.dumps(run(int(sys.argv[1]) if len(sys.argv) > 1 else 120), indent=1))
