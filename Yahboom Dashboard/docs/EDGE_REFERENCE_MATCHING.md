# Edge Image-to-Image Reference Matching

This document explains how the dashboard stops on **your specific water bottle** using image-to-image embedding similarity (not CLIP text labels).

## 1. The problem

You want the robot to stop when it sees **your specific water bottle**, not just any bottle-shaped object.

There are three recognition approaches in this project:

| Approach | Question it answers | Specificity |
|----------|---------------------|-------------|
| **Text labels** (`labels.json` + CLIP) | "Does this look like the word *bottle*?" | Low — any bottle-like object |
| **Pi cache** (`cache_embeddings.json` on Pi) | "Does this vector match my stored bottle vectors?" | High — your exact bottle |
| **Edge reference** (`reference_embeddings.json` on dashboard) | Same math as Pi cache, but matching runs on the **dashboard** | High — your exact bottle |

Edge reference matching lets **Edge test-bench mode** stop on your exact bottle without the Pi cache script or generic text labels.

## 2. What is an embedding?

An **embedding** is a fixed-length vector that MobileCLIP extracts from a camera frame. Similar-looking images produce similar vectors.

```
Camera frame  →  VIT.py (MobileCLIP-S1)  →  float32 vector (128 / 256 / 512 dims)
                                              e.g. [0.02, -0.11, 0.34, ...]
```

- Default on the Pi: **512 dimensions** = **2048 bytes** (`embds3`)
- Smaller sizes: 256 dims (1024 B), 128 dims (512 B)

The dashboard never sees the raw image for matching — only the vector on MQTT topic `yahboom/vit/embedding`.

## 3. Image-to-image vs text-to-image

### Text decoding (display only for Edge stop)

```
Live embedding  →  compare to text prompts ("bottle", "cup", …)  →  softmax confidence %
```

Implemented in `backend/app/services/vit/vit_service.py` (`MobileClipDecoder`). Zero-shot classification — no photo of your bottle is stored. **Edge stop does not use this path.**

### Reference matching (Edge stop)

```
Live embedding  →  compare to stored bottle vectors  →  cosine similarity
```

Implemented in `ReferenceEmbeddingStore` in the same file. Matching uses NumPy only (no torch required).

You "register" your bottle by **saving its embedding vectors** at capture time. At runtime, each live vector is scored against those references.

## 4. End-to-end data flow

```mermaid
sequenceDiagram
  participant Pi as Pi_VIT.py
  participant MQTT as MQTT_Broker
  participant VS as vit_service.py
  participant API as GET_api_vit_status
  participant UI as Browser_hooks.ts
  participant Robot as yahboom_cmd

  Pi->>MQTT: yahboom/vit/embedding
  MQTT->>VS: _handle_embedding()
  VS->>VS: ReferenceEmbeddingStore.match()
  VS->>VS: _record() + update _latest
  UI->>API: poll every 500ms
  API-->>UI: reference_match.hit, similarity_percent
  alt hit and similarity >= 75% and mission armed
    UI->>Robot: auto_off then stop
  end
```

### Steps

1. **Pi** runs `VIT.py` + camera. Every N frames, MobileCLIP publishes an embedding on `yahboom/vit/embedding`.
2. **Dashboard** (`vit_service.py`) subscribes and runs `_handle_embedding()` per message.
3. **Reference matching** normalizes the live vector, compares to all stored samples, picks the best similarity, sets `hit` if above threshold.
4. **`GET /api/vit/status`** exposes `latest.reference_match`.
5. **Browser** (`useEdgeAwareStopLabelEstop` in `src/app/hooks.ts`) polls every 500 ms. On a new decode after START with `hit` and similarity ≥ 75%, sends `auto_off` + `stop`.

## 5. Matching math

Same logic as Pi `check_detection()` in `Yahboom Car/Used/VIT.py`:

1. L2-normalize live and reference vectors
2. Dot product = cosine similarity
3. Best match = highest similarity across all samples
4. Hit = similarity ≥ effective threshold

```
similarity = dot(live_normalized, reference_normalized)
hit = similarity >= effective_threshold
```

### Effective threshold

```python
threshold = float(best_match["threshold"])              # per JSON entry, default 0.70
effective_threshold = max(threshold, stop_threshold)  # stop_threshold default 0.75
hit = best_similarity >= effective_threshold
```

The client also requires `similarity_percent >= 75` in `src/lib/edgeAwareStopLabelEstop.ts`.

## 6. Reference file format

**Default path:** `backend/app/services/vit/reference_embeddings.json`

Same schema as Pi `/home/pi/cache_embeddings.json` from `Yahboom Car/Used/capture_bottle_cache_multi.py`. See `reference_embeddings.json.example` in the same directory.

| Field | Purpose |
|-------|---------|
| `label` | Must match `VIT_REFERENCE_LABEL` (default `bottle`) |
| `sample_id` | Angle/view index (1–6 from multi-angle capture) |
| `data` | Base64 float32 embedding bytes |
| `embedding_dim` | Must match live size (512 for default `embds3`) |
| `threshold` | Per-sample minimum (backend uses `max(threshold, 0.75)`) |

## 7. Creating the reference file

1. Start `VIT.py` on the Pi.
2. Run `capture_bottle_cache_multi.py` on the Pi.
3. Capture **your** bottle at six angles; press Enter after each stable view.
4. Copy `/home/pi/cache_embeddings.json` to `backend/app/services/vit/reference_embeddings.json`.
5. Restart the Flask backend (`npm run dev:backend`).

Multi-angle capture improves recognition when the bottle appears at different poses during autonomous driving.

## 8. What runs where

| Component | Location | Role |
|-----------|----------|------|
| Camera + encoder | Pi (`VIT.py`) | Produces live embeddings |
| Reference file | Dashboard disk | Stores bottle vectors |
| `ReferenceEmbeddingStore` | `vit_service.py` | Image-to-image match |
| `MobileClipDecoder` | `vit_service.py` | Text labels (display only) |
| Stop command | Browser | `auto_off` + `stop` on `yahboom/cmd` |

### Keep `Cae_OFF` on the Pi in Edge mode

When Pi cache-aware is **ON** (`Cae_ON`), a cache hit **suppresses** publishing to `yahboom/vit/embedding`. The dashboard must receive every embedding for edge matching — use **`Cae_OFF`**.

## 9. Configuration

Environment variables in `backend/config.py`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `VIT_REFERENCE_EMBEDDINGS_FILE` | `vit/reference_embeddings.json` | Reference JSON path |
| `VIT_REFERENCE_LABEL` | `bottle` | Label filter |
| `VIT_REFERENCE_MATCH_ENABLED` | `true` | Enable matching |
| `VIT_REFERENCE_DEFAULT_THRESHOLD` | `0.70` | Default per-entry threshold |
| `EDGE_AWARE_REFERENCE_THRESHOLD` | `0.75` | Floor for backend `hit` |

Client: `EDGE_AWARE_MIN_CONFIDENCE = 75` in `edgeAwareStopLabelEstop.ts`.

## 10. API response (`GET /api/vit/status`)

Key fields:

- `reference_ready`, `reference_count`, `reference_file`, `reference_error`
- `latest.match_mode`: `"reference_embedding"` when i2i is primary
- `latest.reference_match`: `{ label, sample_id, similarity, similarity_percent, threshold, hit }`

If `reference_ready` is false, edge stop will not fire.

## 11. Stop trigger (client)

All must be true:

1. Edge-aware stop enabled
2. Test-bench session armed (after START)
3. Edge test-bench mode active
4. New decode (timestamp not already handled)
5. `reference_match.hit === true`
6. `similarity_percent >= 75`
7. Outside 5 s cooldown
8. E-stop not latched

Pre-START detections are ignored.

## 12. UI

The VIT Scene Decoder widget (`Widgets.tsx`) shows **Reference Match**, similarity %, and a **HIT** pill. It warns when the reference file is missing.

## 13. Pi cache vs edge reference

| | Pi cache (`Cae_ON`) | Edge reference |
|--|---------------------|----------------|
| Matching runs on | Pi | Dashboard |
| Reference file | `/home/pi/cache_embeddings.json` | `reference_embeddings.json` |
| Who sends stop | Pi | Dashboard browser |
| MQTT bandwidth | Lower (hits not published) | Every embedding sent |
| Test bench mode | Cache / Hybrid | Edge |

## 14. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `reference_ready: false` | Missing file | Copy cache JSON, restart backend |
| Low similarity | Wrong bottle, angles, or dim mismatch | Re-capture; use `embds3` (512 dims) |
| No dashboard embeddings | Pi cache blocking | `Cae_OFF` |
| Never stops | Not armed or pre-START decode | Press START; check `hit` and ≥ 75% |
| False stops | Threshold too low | Raise thresholds in env or JSON |
| Misses bottle | Threshold too high | More samples; lower threshold slightly |

## 15. Summary

**You capture your bottle as vectors, store them on the dashboard, and each live frame is scored against them. If similarity is high enough after START, the dashboard stops the robot.**

Text labels ask *"is this a bottle?"* Reference matching asks *"is this my bottle?"*
