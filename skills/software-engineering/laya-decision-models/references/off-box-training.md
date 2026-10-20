# Off-box Laya training (Kaggle)

Use when a Laya/SetFit fine-tune must not load the Mini or the Studio (both serve the live agent). Upstream's own recipe targets Kaggle 2×T4: `notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb` (DDP, RLCD) and `notebooks/laya_finetune_typed_decisions_mps.py` (Apple Silicon, `--micro-batch 1 --grad-accum 32`, gradient checkpointing) in github.com/NandhaKishorM/laya.

## Auth

- CLI lives in the sandbox venv: `~/.hermes/cache/laya-exp/venv/bin/kaggle` (`uv pip install --python <venv>/bin/python 'kaggle>=1.7'`).
- Token file: `~/.kaggle/access_token`, mode 600 (`KGAT_…` format, read automatically). Write it with `umask 077`; never cat it, never pass it to a child's context, never bake it into a dataset or kernel.
- Verify with `kaggle config view` (shows `username` + `auth_method: ACCESS_TOKEN`) plus `kaggle competitions list`. `kaggle kernels list --mine` prints "Not found" for an empty account, which says nothing about whether auth works.
- A token pasted into chat stays in history and in the image cache: if the user insists on using it, use it, and recommend they expire and regenerate it on Kaggle afterwards.

## Procedure

1. Scrub the training file before upload: secrets (long hex/base64, `sk-`, `ghp_`, `KGAT_`, API_ID/API_HASH values), IPv4 → consistent placeholders, emails, real hostnames. Passwords in KB rows are usually prose, not key=value ("password <pw>", "uses the <pw> credential"), so regexes keyed on `key=value` miss them: add prose patterns, then grep the scrubbed file for password/passwd/credential and confirm every hit by eye. Same scrub before showing rows in chat (worksheets). Keep row ids and the `split` field. Save a redaction-count report beside it.
2. Upload as a PRIVATE dataset (`dataset-metadata.json` with `isPrivate: true`). Run the step-1 grep on the exact file being uploaded, every time: Kaggle cannot delete one dataset version, so a leaked version means deleting the whole dataset (`kaggle datasets delete <owner>/<slug> -y`) and re-uploading under a new slug. Do that only when no kernel is running against it, then point new kernels at the new slug. Add new training files as new versions of the same private dataset rather than new datasets.
3. Push a PRIVATE kernel: `kernel-metadata.json` with `is_private: true`, `enable_gpu: true`, `enable_internet: true` (needed for `pip install laya` + HF checkpoint download), `dataset_sources` = the private dataset. GPU and internet require a phone-verified Kaggle account; if refused, stop and report the exact error.
4. Inside the kernel: `pip install laya` (record version), `laya-train --help` first, train seeds 0/1/2, also score the BASE checkpoint on the same test rows, write predictions JSONL (`id, split, label, p_drop, model, seed`) + `train_report.json` + the exported config to `/kaggle/working`.
5. Poll `kaggle kernels status <ref>` with short calls every 60–120 s; fetch with `kaggle kernels output <ref> -p <local dir>`. Independent arms (e.g. two losses × 3 seeds) go in separate kernels pushed together, which halves wall time. Have the kernel write calib AND test predictions so thresholds can be re-picked locally; download predictions and reports only and leave checkpoints in the kernel output (~1.6 GB each), noting their paths.
5b. A plain HF-Trainer ModernBERT-large baseline (no Laya) OOMs on a T4 at `max_len 512` with pre-padded batches. Use dynamic padding (`DataCollatorWithPadding`), `per_device_train_batch_size 4` × `gradient_accumulation_steps 16` (same effective 64 as Laya) and gradient checkpointing. Laya's own trainer does not hit this, so reproduce its effective batch, not its micro-batch.
5c. Delete checkpoint/`*ckpt*` dirs at the end of any kernel whose only deliverable is predictions. `kaggle kernels output` downloads everything in `/kaggle/working`, and a multi-GB output stalls or fails the download, costing a full re-run.
5d. Swapping a Laya run's starting weights (vanilla-encoder / random-head diagnostics). `finetune(model_dir=...)` accepts any local dir that holds `rl_agent_config.json`, `model.safetensors`, `encoder/` and `tokenizer/`, so build the hybrid with laya's own functions instead of hand-editing files:
   ```python
   from laya.train import load_checkpoint, save_checkpoint, finetune
   from laya.common import build_model
   laya_model, tok, cfg = load_checkpoint('convaiinnovations/laya')   # cfg['encoder'] == 'answerdotai/ModernBERT-large'
   torch.manual_seed(1234)
   fresh = build_model(cfg).float()        # pretrained=True: vanilla HF encoder + randomly initialised head
   sd_l, sd_f = laya_model.state_dict(), fresh.state_dict()
   assert set(sd_l) == set(sd_f)
   head = [k for k in sd_f if not k.startswith('encoder.')]
   # V1 only: copy Laya's pretrained head (incl. the temperature buffer) onto the vanilla encoder
   with torch.no_grad():
       for k in head: sd_f[k].copy_(sd_l[k])
   save_checkpoint(fresh, tok, cfg, '/tmp/base_V1')   # then finetune(model_dir='/tmp/base_V1', ...)
   ```
   Assert, in the kernel, that the encoder tensors differ from Laya's (max |Δ| > 1e-3, or the swap did nothing) and that V1's head keys are identical to Laya's (max |Δ| == 0). `save_checkpoint` writes fp16 like the published checkpoint, which keeps the comparison like-for-like. The inherited `rl_agent_config.json` (temperatures included) stays as-is to match the earlier Laya arms; AUC and the calib-picked threshold are unaffected by it.
5e. In diagnostic kernels, train each seed into `/tmp/ckpt_<arm>_seed<n>`, score calib+test, then `shutil.rmtree` the checkpoint before the next seed. Rewrite `predictions.jsonl` after EVERY seed, so a late crash still leaves the finished seeds downloadable. Delete the intermediate train/calib JSONL copies from `/kaggle/working` at the end. Laya ModernBERT-large on ~2.6k rows × 4 epochs takes ~9 min/seed on a T4 (~30 min for a 3-seed arm).
6. Compute metrics locally from predictions only (numpy/sklearn, no model loading): same protocol as the Studio run so results compare directly.

## Training-speed notes (upstream browser-agent fine-tune)

- Turn gradient checkpointing OFF when GPU memory allows (~1.25× faster); it only trades speed for memory.
- Don't `torch.compile` variable-length batches: it recompiles per shape (~6× slower).
- After fine-tuning, the 322M and 421M backbones performed equivalently; pick the smaller by latency.
- Re-weight rare classes rather than letting the majority dominate.

At ~200 rows the free GPU mostly proves the pipeline; it pays off once the labelled set reaches thousands (upstream: ~30k questions × 4 epochs ≈ 4–5 h on 2×T4).
