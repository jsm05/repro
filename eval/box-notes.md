# Box notes (written by bootstrap.yml)

- Workflow run: https://github.com/jsm05/repro/actions/runs/44538908817
- SWT-bench harness ref: `be12d4c8e7a5315a6bd60b0cad72736e96a4af3e`
- Gold validation instance: `sympy__sympy-20590`: **resolved**
- Gold validation wall time: 440 s

## Runner

```
image: ubuntu26 20261116.202.1
cpus: 2
               total        used        free      shared  buff/cache   available
Mem:           7.7Gi       819Mi       6.4Gi        40Mi       866Mi       6.9Gi
Swap:          3.0Gi          0B       3.0Gi
docker 29.4.2
```

## Disk

Before freeing space:
```
Filesystem      Size  Used Avail Use% Mounted on
/dev/root        72G   58G   15G  80% /
```
After freeing space:
```
Filesystem      Size  Used Avail Use% Mounted on
/dev/root        72G   36G   37G  50% /
```
After the gold run:
```
Filesystem      Size  Used Avail Use% Mounted on
/dev/root        72G   40G   32G  56% /
```

## Docker images after the gold run

```
exec.eval.x86_64.deec2122407a414e380748.6be69e761c43e05803846d:latest  4.44GB
exec.env.x86_64.deec2122407a414e380748:latest  3.96GB
exec.base.x86_64:latest  2.21GB
ubuntu:22.04  120MB
```

## Dataset

```json
{"dataset": "nmuendler/SWT-Bench_Verified_bm25_27k_zsp", "splits": {"test": 433}, "split_used": "test", "columns": ["repo", "instance_id", "base_commit", "patch", "test_patch", "problem_statement", "hints_text", "created_at", "version", "FAIL_TO_PASS", "PASS_TO_PASS", "environment_setup_commit", "difficulty", "hits", "text"], "n_ids": 433}
```

## Validation log (last 40 lines)

```
Generating dev split: 100%|██████████| 23/23 [00:00<00:00, 5767.95 examples/s]

Generating test split:   0%|          | 0/300 [00:00<?, ? examples/s]
Generating test split: 100%|██████████| 300/300 [00:00<00:00, 33321.62 examples/s]
Running 1 unevaluated instances...
Running 1 instances...

  0%|          | 0/1 [00:00<?, ?it/s]
100%|██████████| 1/1 [07:13<00:00, 433.78s/it]
100%|██████████| 1/1 [07:13<00:00, 433.78s/it]
Building base image exec.base.x86_64:latest
Base image exec.base.x86_64:latest built successfully.
Building env image exec.env.x86_64.deec2122407a414e380748:latest
Env image exec.env.x86_64.deec2122407a414e380748:latest built successfully.
Base image exec.base.x86_64:latest already exists, skipping build.
Env image exec.env.x86_64.deec2122407a414e380748:latest already exists, skipping build.
Base image exec.base.x86_64:latest already exists, skipping build.
Env image exec.env.x86_64.deec2122407a414e380748:latest already exists, skipping build.
Base image exec.base.x86_64:latest already exists, skipping build.
Env image exec.env.x86_64.deec2122407a414e380748:latest already exists, skipping build.
Base image exec.base.x86_64:latest already exists, skipping build.
Env image exec.env.x86_64.deec2122407a414e380748:latest already exists, skipping build.
Base image exec.base.x86_64:latest already exists, skipping build.
Env image exec.env.x86_64.deec2122407a414e380748:latest already exists, skipping build.
All instances run.
Cleaning cached images...
Removed 0 images.

  0%|          | 0/1 [00:00<?, ?it/s]
100%|██████████| 1/1 [00:00<00:00, 214.30it/s]
Total instances: 1
Instances completed: 0
Mean coverage: 1.0
Mean coverage delta: 0.0
Instances resolved: 1
Instances unresolved: 0
Instances with errors: 0
Instances still running: 0
Still existing images: 1
Report written to evaluation_results/gold.validate-gold.json
```

## Raw result files

### gold.validate-gold.json

```json
{
    "total_instances": 1,
    "completed_instances": 0,
    "resolved_instances": 1,
    "unresolved_instances": 0,
    "error_instances": 0,
    "Mean coverage": 1.0,
    "Mean coverage delta": 0.0,
    "unstopped_instances": 0,
    "completed_ids": [],
    "resolved_ids": [
        "sympy__sympy-20590"
    ],
    "unresolved_ids": [],
    "error_ids": [],
    "unstopped_containers": [],
    "unremoved_images": [
        "exec.eval.x86_64.deec2122407a414e380748.6be69e761c43e05803846d:latest"
    ]
}

```
