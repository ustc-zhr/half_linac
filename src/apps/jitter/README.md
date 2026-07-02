# Jitter App Integration

`jitter_analysis/` is an externally maintained Git repository embedded for the
Jitter Analysis GUI.

The `half_linac` integration layer should live in this directory, outside the
embedded repository. Keep launcher wrappers, compatibility glue, and local
runtime notes here so `jitter_analysis/` can be updated with:

```sh
git -C src/apps/jitter/jitter_analysis pull --ff-only
```

