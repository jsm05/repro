"""Run inside the SWT-bench checkout (cwd) with the harness installed. Not imported by
Repro itself: swt_harness.image_keys() runs it as a script.

stdin:  {"records": [dataset records], "build": bool}
stdout: a line "REPRO_JSON {instance_id: {"image", "env_image", "base_image"}}"
"""

import json
import os
import sys

sys.path.insert(0, os.getcwd())

from src.dataset import swt_to_swt_instance  # noqa: E402
from src.docker_build import build_instance_image_from_exec_spec  # noqa: E402
from src.exec_spec import make_exec_spec  # noqa: E402


def main():
    req = json.load(sys.stdin)
    out = {}
    for rec in req["records"]:
        rec = dict(rec)
        if not str(rec.get("patch", "")).startswith("<patch>"):
            rec["patch"] = "<patch>\n" + rec.get("patch", "") + "\n</patch>"
        spec = make_exec_spec(swt_to_swt_instance(rec))
        if req.get("build"):
            build_instance_image_from_exec_spec(spec, False)
        out[spec.instance_id] = {"image": spec.instance_image_key,
                                 "env_image": spec.env_image_key,
                                 "base_image": spec.base_image_key}
    print("REPRO_JSON " + json.dumps(out))


if __name__ == "__main__":
    main()
