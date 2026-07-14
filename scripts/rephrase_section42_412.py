"""Rephrase Sections 4.2–4.12 to match the user's heading outline."""
from pathlib import Path

REPORT = Path(r"c:\Users\malco\Github\YahboomProject\ITP_Group10_FinalReport.md")
START = "## 4.2 Overall Conceptual Design"
END = "## 6. Testing, Evaluation and Outlook"

CONTENT = r"""## 4.2 Overall Conceptual Design

The prototype was structured as three cooperating layers: robot, communication and client. Each layer
owned a distinct responsibility, and data crossed layer boundaries only through named MQTT topics or
the WebRTC video channel.

```mermaid
flowchart LR
  subgraph Robot["Yahboom Robot Layer"]
    CAM[webrtc_server.py]
    VIT[VIT.py]
    MQTTROS[mqtt_ros_node.py]
    LIDAR[lidar_safety_node.py]
  end
  subgraph Comm["Communication Layer"]
    MQTT[MQTT topics]
    RTC[WebRTC video]
  end
  subgraph Client["Cloud-Side Client Layer"]
    DASH[Dashboard UI]
    VITS[vit_service.py]
    BH[backhaul_delay.py]
  end
  CAM --> MQTT
  CAM --> RTC
  VIT --> MQTT
  MQTTROS --> MQTT
  LIDAR --> MQTT
  MQTT --> VITS
  MQTT --> DASH
  VITS --> BH
  BH --> DASH
  DASH --> MQTT
```

### 4.2.1 Yahboom Robot Layer

The robot layer ran on the Raspberry Pi and included the camera, LiDAR, motor controller and ROS 2
runtime. Five scripts carried the onboard workload:

| Script | Main responsibility |
| --- | --- |
| webrtc_server.py | Camera capture, WebRTC streaming, JPEG frames to MQTT |
| VIT.py | MobileCLIP encoding, cache comparison, offloading decisions |
| capture_bottle_cache_multi.py | Six-angle reference embedding capture |
| mqtt_ros_node.py | MQTT-to-ROS motion bridge and autonomous navigation |
| lidar_safety_node.py | LiDAR monitoring and hard/soft stopping |

Safety reflexes, motor control and image encoding remained on the robot because they are
time-critical and because camera data was already local. Only text-label matching was delegated to
the client.

### 4.2.2 Communication Layer

Two protocols carried different traffic types. MQTT transported commands, embeddings, recognition
results, cache events and safety status. Primary topics were:

● yahboom/cmd — movement, estop, auto mode, cache-aware commands
● yahboom/camera/frame — JPEG frames for VIT.py
● yahboom/vit/embedding — embeddings on cache miss
● yahboom/vit/status — encoder status
● yahboom/vit/result — decoded recognition results
● yahboom/detect/status — local cache detection events
● yahboom/safety/status — LiDAR safety feedback

WebRTC carried the continuous live video stream. Keeping video off the MQTT bus prevented large
media payloads from competing with stop commands and compact embeddings.

### 4.2.3 Cloud-Side Client Layer

The laptop client stood in for a remote cloud server. The Yahboom Dashboard backend performed
decoding, display and command issuance, while backhaul_delay.py inserted configurable hop delays on
non-video MQTT traffic [1].

The client had four roles: operator interface, embedding reception, MobileCLIP label matching in
vit_service.py, and stop-command publication when the bottle was detected. Decoding meant
interpreting an image embedding against text embeddings from labels.json—not reconstructing the
original camera image [3]. WebRTC was excluded from hop delay so monitoring stayed responsive under
high simulated latency.

### 4.2.4 Normal Cloud-Recognition Workflow

When cache-aware mode was off, or when a cache miss occurred, recognition followed the cloud path:

1. webrtc_server.py publishes a JPEG frame on yahboom/camera/frame.
2. VIT.py decodes the frame and generates a MobileCLIP-S1 embedding.
3. VIT.py publishes the embedding on yahboom/vit/embedding.
4. The client receives the message after a simulated incoming hop.
5. vit_service.py matches the embedding against text labels.
6. The result is published on yahboom/vit/result after a simulated outgoing hop.
7. At ≥75% bottle confidence, the dashboard sends auto_off then stop on yahboom/cmd.
8. mqtt_ros_node.py publishes a zero-velocity ROS 2 Twist.

This is the full encode-offload-decode-actuate loop used whenever local cache does not handle
recognition.

### 4.2.5 Cache-Aware Offloading Workflow

When the client sends Cae_ON, VIT.py enables cache-aware mode:

1. A camera frame is encoded to a MobileCLIP embedding (same as the cloud path).
2. The live embedding is compared against all entries in cache_embeddings.json.
3. On cache hit (similarity ≥ threshold for the best match):
   ● the embedding is not sent to the client;
   ● after three consecutive hits, VIT.py publishes stop and auto_off; and
   ● an event is published on yahboom/detect/status.
4. On cache miss:
   ● the embedding follows the normal cloud path; and
   ● the dashboard may still stop the robot after remote decoding.

Cache-aware mode persists until Cae_OFF. A local stop does not disable caching; the operator must
explicitly end cache-aware operation.

CEG1010 Integrative Team Project  (2025-07)  Page 13



## 4.3 Camera Capture and Live Video Streaming

The camera subsystem supplied frames to both operator preview and MobileCLIP encoding through a
single capture process.

### 4.3.1 Camera Resource-Sharing Challenge

Live streaming and inference both needed the same physical camera. Opening the device from multiple
processes caused frame drops and access conflicts on the Raspberry Pi. Exclusive ownership was
assigned to webrtc_server.py; VIT.py consumed relayed frames on yahboom/camera/frame instead of
opening the sensor directly.

### 4.3.2 Camera Capture Using webrtc_server.py

The script opens camera index 0 at 320×240 and 15 FPS. A camera_worker thread reads frames under a
lock, JPEG-compresses them at quality 70, and publishes to yahboom/camera/frame. The same latest
frame buffer feeds WebRTC, so preview and inference share one capture path.

### 4.3.3 WebRTC Video Transmission

webrtc_server.py hosts an aiohttp server on port 8080. The browser negotiates WebRTC via SDP
offer/answer at /offer. CameraVideoTrack returns the latest shared frame to the WebRTC stack. This
path is independent of the MQTT embedding pipeline and was not subject to simulated backhaul delay.

### 4.3.4 Camera-Frame Delivery to VIT.py

VIT.py subscribes to yahboom/camera/frame, decodes jpg_b64 with OpenCV, and buffers BGR frames for
vit_worker. MobileCLIP runs on every fifth frame (INFERENCE_EVERY_N_FRAMES = 5), separating capture
rate from inference load.

### 4.3.5 Resolution and Frame-Rate Trade-Offs

Higher resolution, frame rate or inference frequency increased Pi load and could cause video stutter.
The chosen settings—320×240, 15 FPS, inference every fifth frame—balanced bottle detection coverage
against stable streaming and motor responsiveness. When performance degraded, inference frequency or
embedding dimension was reduced before raising camera resolution.

## 4.4 Robot-Side MobileCLIP Image Encoding

VIT.py converted camera frames into compact embeddings for local cache lookup and optional cloud
offload.

### 4.4.1 Selection of MobileCLIP-S1

The project used MobileCLIP-S1 (datacompdr weights) via open_clip. Larger CLIP models exceed
Raspberry Pi memory and latency budgets. MobileCLIP-S1 pairs a hybrid MCi1 image encoder with a
compact text encoder, giving a practical latency–accuracy trade-off for on-device encoding [3].

### 4.4.2 Role of VIT.py

VIT.py is the robot-side perception hub. It encodes frames, compares against the bottle cache,
publishes embeddings on miss, issues local stops on hit, and handles runtime commands (embds1/2/3,
Cae_ON, Cae_OFF) on yahboom/cmd.

### 4.4.3 Image Preprocessing and Encoding

For camera frame X, the encoder produces:

e_I = f_I(X)

where f_I is the MobileCLIP-S1 image encoder. The pipeline is:

BGR → RGB → PIL → preprocessing → encode_image → L2 normalise → truncate → L2 normalise again

Inference runs every fifth frame to limit CPU use alongside WebRTC and ROS 2 workloads.

### 4.4.4 L2 Embedding Normalisation

Embeddings are scaled to unit length:

e = e / ||e||_2,  where  ||e||_2 = sqrt( Σ e_j² )

Normalisation makes dot products equivalent to cosine similarity and keeps cache thresholds stable
across sessions.

### 4.4.5 Configurable Embedding Dimensions

Truncation reduces MQTT payload size. Three settings are supported:

| Command | Payload size | Dimensions |
| --- | --- | --- |
| embds1 | 512 bytes | 128 |
| embds2 | 1024 bytes | 256 |
| embds3 | 2048 bytes | 512 |

Default is embds3 (512 dimensions). Vectors are re-normalised after truncation because slicing
changes magnitude.

### 4.4.6 Embedding Payload Construction

On cache miss, VIT.py publishes JSON on yahboom/vit/embedding containing raw_bytes,
embedding_dim, base64 float32 data, frame_id, timestamp, image_file_size, and cache metadata
(cache_hit, similarity, threshold). The image_file_size field links embedding traffic back to the
source frame cost.

CEG1010 Integrative Team Project  (2025-07)  Page 14



## 4.5 Cloud-Side MobileCLIP Decoding and Label Matching

vit_service.py completed zero-shot label matching on the client using the same MobileCLIP model
family as the robot encoder.

### 4.5.1 Role of vit_service.py

vit_service.py runs in the Dashboard backend. It subscribes to VIT MQTT topics, decodes embeddings,
maintains session history, and publishes results for the UI and stop-logic hooks.

### 4.5.2 MQTT Embedding Reception

On connect, the service subscribes to yahboom/vit/embedding. Each message passes through
backhaul_delay.apply() before parsing, simulating uplink latency from robot to cloud [1].

### 4.5.3 Embedding Parsing and Dimension Validation

Payloads may be JSON envelopes or legacy raw float32 bytes. Base64 data is decoded to a float32
vector; declared embedding_dim must match byte length (512, 1024 or 2048 bytes for 128, 256 or 512
dimensions). Mismatches raise validation errors rather than silent mis-decoding.

### 4.5.4 Label Configuration Using labels.json

Text labels load from labels.json for zero-shot classification. The label "bottle" is the mission stop
target. Labels are editable from the dashboard without changing robot code because decoding is
entirely client-side.

### 4.5.5 MobileCLIP Text-Embedding Generation

At startup, MobileClipDecoder tokenises all labels and runs encode_text. Text embeddings are
L2-normalised. For truncated image vectors, the matching prefix of each text embedding is sliced
and re-normalised so dimensions align during comparison.

### 4.5.6 Image-to-Text Similarity Matching

For normalised image embedding e_I and text embeddings {e_Ti}, label scores are:

s_i = e_I · e_T_i

With unit vectors, this equals cosine similarity. The highest-scoring label is the decoded class.

### 4.5.7 Softmax Confidence Calculation

Scores become confidence percentages via scaled softmax:

p_i = softmax(100 · s_i)

Results include top-k labels. A 60% decode threshold flags low confidence in published results; the
dashboard requires 75% on "bottle" before issuing a stop command.

### 4.5.8 Recognition-Result Publication

Decoded output on yahboom/vit/result includes top_label, top_confidence, ranked results,
embedding_size, embedding_dim and timestamp. An outgoing backhaul delay is applied before
publication.

### 4.5.9 Dashboard Stop-Command Handling

When bottle confidence reaches 75%, cloudAwareStopLabelEstop.ts sends auto_off then stop on
yahboom/cmd. This uses a soft stop (not estop_on), so the operator can resume without clearing a
hard emergency latch.

CEG1010 Integrative Team Project  (2025-07)  Page 15



## 4.6 Cache Creation and Reference-Embedding Storage

Reference embeddings were captured offline and stored for runtime cache comparison.

### 4.6.1 Limitations of a Single Reference Image

One cached vector represents a single pose, distance and lighting condition. During testing, a
single-image cache missed frequently when the live view differed in angle or occlusion, forcing
unnecessary cloud offloads.

### 4.6.2 Multi-Angle Bottle Data Collection

Six snapshots of the black bottle were taken at varied angles and distances—front, left, right,
slight rotation, near and far. Multiple references improved coverage of appearance variation without
retraining MobileCLIP.

### 4.6.3 Role of capture_bottle_cache_multi.py

This helper script listens to yahboom/vit/embedding while VIT.py runs. The operator repositioned the
bottle and pressed Enter after each stable view; the latest embedding was saved to
/home/pi/cache_embeddings.json with automatic backup of any existing file.

### 4.6.4 Cache-File Structure and Metadata

Each entry stores label ("bottle"), sample_id, model, pretrained, embedding_dim, threshold,
normalised flag, dtype, source frame, base64 float32 data and created_at. The file format is
{ "objects": [ ... ] }. VIT.py loads all "bottle" entries and normalises vectors at startup.

### 4.6.5 Similarity-Threshold Configuration

Each entry carries a similarity threshold (default 0.70). The best match across all samples must
exceed its threshold for a hit. Per-entry thresholds allow future multi-class extension; the
prototype used a single bottle label.

## 4.7 Cache-Aware Offloading Implementation

Cache logic in VIT.py decides whether to stop locally or forward an embedding to the client.

### 4.7.1 Cache Logic Integrated into VIT.py

On startup, VIT.py loads cache_embeddings.json and publishes on yahboom/cache_aware/ready. Comparisons
run only after Cae_ON; when cache-aware mode is off, every embedding is published normally.

### 4.7.2 Live-to-Cache Embedding Comparison

Each inferred frame produces a live embedding compared against every cached bottle vector when
cache_ready and test_active are both true.

### 4.7.3 Cosine-Similarity Calculation

With L2-normalised vectors:

similarity = e_live · e_cache

NumPy dot products implement this efficiently on the Pi for six reference samples per frame.

### 4.7.4 Best-Match Selection

The highest similarity across all cached samples determines the match, regardless of sample_id. This
supports recognition from any stored viewpoint during autonomous exploration.

### 4.7.5 Cache-Hit Decision

A hit occurs when similarity ≥ threshold for the best match. The embedding is not published to
yahboom/vit/embedding; terminal logs show confidence as similarity × 100%.

### 4.7.6 Cache-Miss and Cloud Fallback

On miss, VIT.py publishes the embedding with cache_hit=false plus similarity and threshold metadata.
Remote decoding can still identify the bottle and trigger a dashboard stop.

### 4.7.7 Consecutive-Hit Requirement

CONSECUTIVE_HITS_REQUIRED = 3 consecutive hits are needed before a stop command, filtering
single-frame similarity spikes.

### 4.7.8 Detection Latch and Cooldown

After a stop, a latch blocks repeat sequences until UNLATCH_MISSES_REQUIRED = 5 consecutive misses.
DETECTION_COOLDOWN_S = 2.0 s limits re-triggers. Stop is repeated STOP_REPEAT_COUNT = 8 times at
50 ms intervals for reliable MQTT delivery.

### 4.7.9 Persistent Cae_ON and Cae_OFF State

Only Cae_ON and Cae_OFF control cache-aware mode. stop and auto_off after detection do not disable
caching; the operator sends Cae_OFF to end a cache-aware session.

CEG1010 Integrative Team Project  (2025-07)  Page 16



## 4.8 Simulated Cloud Hops and Latency

backhaul_delay.py modelled wide-area latency because the client ran on a local laptop rather than
commercial cloud infrastructure [1][2].

### 4.8.1 Purpose of the Cloud Simulation

The simulation let the team test recognition responsiveness under configurable hop counts without
deploying to a remote cloud. It separated functional correctness on a LAN from the latency effects
that offloading introduces in practice.

### 4.8.2 Incoming Hop Delay

On receive, non-video MQTT messages pass through backhaul_delay.apply(), which samples a
gamma-distributed sleep modelling robot-to-cloud uplink latency.

### 4.8.3 Cloud-Side Decoding Time

After the incoming hop, vit_service.py runs MobileCLIP text matching. T_decode depends on client
hardware and embedding dimension and adds to—but is not part of—the hop model.

### 4.8.4 Outgoing Hop Delay

Before publishing yahboom/vit/result or sending stop commands, a second sampled hop models
cloud-to-robot downlink delay.

### 4.8.5 End-to-End Cloud-Latency Formula

T_cloud ≈ T_in + T_decode + T_out + T_cmd

Each hop delay uses:

shape = floor( (1 + 1.28 · M_BS / M_GW) · k1 + (h − 1) · k2 )

scale = a + packet_size_bits · k3

T_hop ~ Gamma(shape, scale) × 1000 ms

Defaults: h = 30, M_BS = 5, M_GW = 1, k1 = 1, k2 = 2, k3 = 1×10⁻⁸, a = 1×10⁻⁵,
packet_size_bits = 12000.

### 4.8.6 Local Cache Response-Time Formula

T_cache ≈ T_encode + T_compare + T_stop

No remote hops appear in this path.

### 4.8.7 Latency Saved by a Cache Hit

T_saved ≈ T_in + T_decode + T_out + T_cmd

Savings grow with hop count h because each additional hop increases the gamma shape parameter.

### 4.8.8 Low- and High-Latency Dry Runs

Low-hop settings verified functional correctness with near-immediate cloud decodes. High-hop settings
showed cache hits stopping the robot before cloud results returned.

### 4.8.9 Cloud Detection Following a Cache Miss

When local cache failed, embeddings still reached vit_service.py and cloud decoding could stop the
robot after the full round trip. The cache is therefore an optimisation layer, not a single point of
failure.

## 4.9 MQTT and ROS 2 Command and Control

mqtt_ros_node.py bridges dashboard and VIT commands into ROS 2 motor actuation.

### 4.9.1 Role of mqtt_ros_node.py

The node subscribes to yahboom/cmd, tracks movement and auto-mode state, and publishes
geometry_msgs/Twist on cmd_vel. It is the sole translator from MQTT text commands to motor targets.

### 4.9.2 MQTT Command Reception

Commands arrive as plain text or JSON on yahboom/cmd: movement (fwd, left, right, stop, etc.), servo
motion, auto_on/auto_off, estop_on/estop_off and auto_soft_stop. VIT.py listens separately for
embds and Cae commands to avoid handler conflicts.

### 4.9.3 Conversion to ROS 2 Twist Messages

Movement commands set target linear and angular velocities published as Twist messages on cmd_vel
for the Yahboom motor driver.

### 4.9.4 Linear and Angular Velocity Control

Manual mode targets LINEAR_SPEED = 0.5 m/s and ANGULAR_SPEED = 1.0 rad/s. Autonomous mode uses
lower forward speeds and separate turn gains. Servo commands adjust camera aim independently of base
motion.

### 4.9.5 Command Publication Rate

Velocity is published at PUBLISH_RATE = 20 Hz so intermittent MQTT delivery does not leave the
robot coasting with a stale non-zero velocity.

### 4.9.6 Velocity Ramping

LINEAR_STEP = 0.02 and ANGULAR_STEP = 0.05 per cycle ramp current speeds toward targets, reducing
wheel slip during direction changes.

### 4.9.7 Stop-Command Processing

stop zeros velocities immediately. estop_on latches estop_active and blocks movement until estop_off.
auto_soft_stop halts without latching—used for autonomous explore and bottle-detection stops.

CEG1010 Integrative Team Project  (2025-07)  Page 17



## 4.10 Autonomous Movement and LiDAR Safety

Local LiDAR processing provides obstacle reflexes independent of cloud recognition delay [2].

### 4.10.1 LiDAR Scan Processing

lidar_safety_node.py subscribes to LaserScan on /scan and derives front, side and rear distances.
Invalid readings outside sensor limits are discarded before any decision.

### 4.10.2 Sector-Based Distance Measurement

The front safety cone spans ±20°. mqtt_ros_node.py uses additional sectors (front-left, front-right,
side, rear) for autonomous gap finding.

### 4.10.3 Obstacle Detection

An obstacle is confirmed when CONFIRM_POINTS = 8 valid front readings fall below block distance.
WARNING_DISTANCE = 0.60 m triggers advisory status; BLOCK_DISTANCE = 0.35 m triggers stopping.

### 4.10.4 Gap-Width Estimation

In auto mode, candidate turn angles from −100° to +100° (4° steps) are scored by clearance. Paths
narrower than MIN_GAP_WIDTH_M = 0.195 m are rejected.

### 4.10.5 Direction Selection and Recovery

Clear front → forward motion scaled by clearance. Blocked front → steer to best gap, turn in place,
or left/right recovery. Both front and rear blocked → stop with auto_all_blocked_front_and_rear.

### 4.10.6 Local Hard Emergency Stop

In manual mode, a confirmed front obstacle publishes estop_on and stop. mqtt_ros_node.py latches
estop_active until estop_off; a 30-second re-arm grace period follows release.

### 4.10.7 Autonomous Soft Stop

In auto mode, the same obstacle triggers auto_soft_stop instead of estop_on. Bottle-detection stops
use the same non-latching pattern.

### 4.10.8 Safety and Command Priority

Priority (highest to lowest):

1. LiDAR hard e-stop (manual mode obstacle)
2. Estop latch
3. Soft stop (LiDAR auto mode or bottle detection)
4. Autonomous movement
5. Manual movement

Local safety always overrides delayed visual-recognition results.

## 4.11 System Integration Challenges and Solutions

Field integration exposed issues that isolated component tests did not reveal.

### 4.11.1 Video Stuttering and Processing Load

Concurrent WebRTC, MQTT JPEG publishing and MobileCLIP inference caused occasional stutter.
Mitigation: inference every fifth frame, 320×240 resolution, and separate threads/processes for
video and VIT workloads.

### 4.11.3 Embedding-Dimension Mismatch

Robot and client mismatched embedding sizes caused decode failures. The embds1/embds2/embds3
commands and matching EMBEDDING_BYTES_TO_DIMS tables on both sides keep payload size aligned.

### 4.11.4 Weak Single-View Cache Recognition

A single cached image missed at unfamiliar angles. Six-angle capture via
capture_bottle_cache_multi.py improved hit rate.

### 4.11.5 One-Frame False Detection

Transient similarity spikes could trigger premature stops. CONSECUTIVE_HITS_REQUIRED = 3 required
stable detections.

### 4.11.6 Repeated Stop Commands

Without latching, every post-stop cache hit re-flooded MQTT with stop and auto_off. The detection
latch, cooldown and bounded repeat publish (8× at 50 ms) prevented spam while ensuring delivery.

### 4.11.7 Cache-Aware State Resetting

Cae_OFF resets test_active, hit streaks and the detection latch. The dashboard test bench clears
stop state at the start of each new run.

### 4.11.8 Invalid Cache Configuration File

Missing or empty cache_embeddings.json sets cache_ready=false and raises a load error rather than
silently disabling cache-aware mode.

### 4.11.9 Autonomous and Safety Command Conflict

Bottle detection sends auto_off and stop while LiDAR may send auto_soft_stop concurrently.
Separating soft stop from estop_on lets recognition and obstacle halts coexist without a manual
estop reset.


## 4.12 Application of Engineering Knowledge and Technical Learning

This section reflects on how curriculum concepts were applied across the prototype. Implementation
detail appears in Sections 4.2–4.11; here the focus is on the engineering principles learned.

### 4.12.1 Embedded-Systems Knowledge

The project applied resource-bounded design on a Raspberry Pi: exclusive peripheral ownership
(single camera process), inference decimation, modular processes for video/VIT/motion/LiDAR, and
deliberate limits on resolution and frame rate. The outcome was a workable concurrent workload
without hardware upgrade—demonstrating that scheduling and partitioning matter as much as raw
compute when integrating streaming, inference and real-time control.

### 4.12.2 Artificial-Intelligence Knowledge

Rather than training a new model, the team deployed a pretrained vision-language encoder (MobileCLIP-S1)
as a feature extractor and partitioned the pipeline: image encoding on the robot, text matching on
the client [3]. Curriculum concepts—transfer learning, inference budgeting, embedding truncation as a
bandwidth knob, threshold-based decisions and hybrid local/remote inference—were applied at the
systems level rather than in model development.

### 4.12.3 Linear Algebra and Similarity Measurement

Recognition relied on comparing L2-normalised embedding vectors. Cosine similarity reduced to dot
products after normalisation (similarity = e_live · e_cache), connecting cache logic directly to
vector geometry taught in the engineering programme. The same principle underpinned client-side
image-to-text matching. Temporal filters (consecutive hits, detection latch) added a time dimension
to geometric decisions, reducing false stops from outlier frames.

### 4.12.4 Computer-Networking Knowledge

Traffic was segregated by purpose: MQTT for control and semantic features, WebRTC for continuous
video. Topic naming under yahboom/ gave a stable contract between robot and client. QoS choices
reflected priority—confirmed delivery where needed, fire-and-forget for high-rate frames. The gamma
backhaul model applied networking concepts (multi-hop delay, right-skewed latency distributions) to
make local testing representative of wide-area conditions [1][2].

### 4.12.5 Cloud and Distributed-Computing Knowledge

The laptop client functioned as a logical remote compute node. Task placement followed latency
sensitivity: reflexes and encoding stayed on the robot; label matching moved to the client. The R2X
timing inequality (uplink + remote + downlink vs local alternative) was evaluated experimentally by
varying hop count and comparing cloud-path delay against cache-hit response [2]. This illustrated
distributed placement, payload design and fallback paths without requiring a commercial cloud
deployment.

### 4.12.6 Cache-System Knowledge

The bottle cache was content-addressable storage in embedding space: live vectors mapped to stored
reference vectors rather than filenames or URLs. Design choices mirrored general caching practice—
reference diversity (six views), threshold tuning, early-exit before remote lookup, and policy
(Cae_ON/Cae_OFF) separated from individual hit/miss outcomes. The cache optimised the recognition
path without replacing cloud decoding entirely.

### 4.12.7 ROS 2 and Robotics Knowledge

ROS 2 provided typed publish/subscribe middleware for onboard sensing and actuation. mqtt_ros_node.py
and lidar_safety_node.py consumed LaserScan on /scan and published Twist on /cmd_vel—the standard
pattern for differential-drive platforms. Perception (VIT.py) and video (webrtc_server.py) used MQTT
and WebRTC instead, showing how ROS can scope to locomotion and geometric sensing while higher-level
services use other transports.

Key robotics patterns applied included: (1) an MQTT–ROS bridge that converts event-based remote
commands into a 20 Hz velocity stream, because differential drives need continuous Twist publication;
(2) asynchronous sensor callbacks updating internal state, with a fixed-rate controller mapping state
to actuators; (3) velocity ramping to limit jerk; (4) a closed safety loop where lidar_safety_node.py
publishes estop_on or auto_soft_stop back to yahboom/cmd, reusing the same actuation path as operator
and recognition stops; and (5) explicit state machines for manual, auto and latched estop modes. These
patterns align with the literature recommendation to keep reflex actions local while offloading
heavier deliberation remotely [2].

### 4.12.8 System Integration and Trade-Off Analysis

No single setting maximised accuracy, latency, bandwidth, safety and usability simultaneously. Higher
embedding dimension and inference rate improved recognition but loaded the Pi and MQTT. Lower
resolution stabilised video but reduced visual detail. Aggressive cache thresholds cut cloud traffic
but increased misses on unseen views. High hop counts highlighted cache value while slowing cloud
fallback.

The final design is a documented compromise: local LiDAR safety, semantic features instead of raw
video offload, cache-aware early exit, simulated backhaul for observable latency, and cloud decoding
as fallback. Quantitative evaluation under varying hop settings is reported in Section 6.


"""


def main() -> None:
    text = REPORT.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit("Section markers not found")
    before = text[: text.index(START)]
    after = text[text.index(END) :]
    REPORT.write_text(before + CONTENT + after, encoding="utf-8")
    downloads = Path(r"c:\Users\malco\Downloads\ITP_Group10_FinalReport.md")
    downloads.write_text(before + CONTENT + after, encoding="utf-8")
    print("Rephrased 4.2–4.12 in both report copies.")


if __name__ == "__main__":
    main()
