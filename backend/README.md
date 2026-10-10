# mcuaccel_backend

Stdlib + mcugrad (`scripts/env.sh` puts `third_party/mcugrad` and `backend/` on PYTHONPATH).

- `graphs.build_int8_graph(model)` / `load_int8_graph(model)`: the int8 graph of `mnist_cnn` or
  `cifar_mobilenet`, built exactly like mcugrad `bench_codegen.build()` (calib 512, per-channel,
  seed 0); cached as JSON in `build/graphs/`.
- `layers.Layer`, `from_node(graph, node)`, `supported(graph, node)` (True or a reason string),
  `check(layer)`: one accelerated op and the SPEC §3 / DESIGN §6.4 limits.
- `pack.pack_weights(layer, P, o_s, nch) -> (bytes, w_rows, rows_per_tile)`, `pack_params(layer, o_s, nch)`.
- `desc.build_descriptors(layer, P, x_addr, y_addr, w_addr, p_addr, next_addr, desc_addr=0)`:
  18-word descriptors, one per split (NEXT chains through `desc_addr + 72k`, last gets `next_addr`).
- `split.split_plan`, `split.build_commands(...) -> [Command]` (weights/params/descriptor bytes and
  addresses per split), `split.write_image(mem, cmds)`.
- `exec.run_descriptor(mem, desc_addr, P, stats=False, trace=None)`: pure-Python executor; the
  module docstring is the normative statement of the corner-case semantics.

Tests: `make test` (300 random layers); `MCUACCEL_SEEDS=1000` for the C2a-style campaign,
`MCUACCEL_SHAPES=full` for the DESIGN §10.2 shape ranges. `-m "not slow"` skips the graph tests.
