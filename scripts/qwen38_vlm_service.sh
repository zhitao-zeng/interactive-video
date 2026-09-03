#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)

CONTAINER_NAME=${QWEN38_VLM_CONTAINER_NAME:-novel-manga-qwen38-vlm}
IMAGE=${QWEN38_VLM_IMAGE:-harbor-contest.4pd.io/lizhuoju/vllm-base:v0.23.0-cu129@sha256:aef95452043c94f0f98f2ff911e9d14c3159fc22af48f337c67a58fdf64607cd}
MODEL_DIR=${QWEN38_VLM_MODEL_DIR:-/mnt/disk1/zengzhitao/models/Qwen3.8-27B-INT8-W8A16-MTP}
CACHE_DIR=${QWEN38_VLM_CACHE_DIR:-$PROJECT_ROOT/.runtime/qwen38-vlm}
HOST=${QWEN38_VLM_HOST:-127.0.0.1}
PORT=${QWEN38_VLM_PORT:-18120}
GPUS=${QWEN38_VLM_GPUS:-${QWEN38_VLM_GPU:-}}
GPU_MEMORY_UTILIZATION=${QWEN38_VLM_GPU_MEMORY_UTILIZATION:-0.62}
MIN_FREE_MIB=${QWEN38_VLM_MIN_FREE_MIB:-43000}
MAX_MODEL_LEN=${QWEN38_VLM_MAX_MODEL_LEN:-65536}
MAX_NUM_BATCHED_TOKENS=${QWEN38_VLM_MAX_NUM_BATCHED_TOKENS:-8192}
STARTUP_TIMEOUT=${QWEN38_VLM_STARTUP_TIMEOUT:-900}
MODEL_NAME=${QWEN38_VLM_MODEL_NAME:-Qwen3.8-27B-Project}
MM_LIMIT=${QWEN38_VLM_MM_LIMIT:-'{"image":{"count":8,"width":2048,"height":2048},"video":0}'}
OWNER_LABEL=novel-manga-video-qwen38-vlm

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

validate_common() {
  require_command docker
  require_command curl
  require_command jq
  [[ "$CONTAINER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "invalid container name"
  [[ "$HOST" == "127.0.0.1" || "$HOST" == "localhost" ]] || die "QWEN38_VLM_HOST must remain loopback-only"
  [[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT >= 1024 && PORT <= 65535)) || die "invalid QWEN38_VLM_PORT"
  [[ "$MAX_MODEL_LEN" =~ ^[0-9]+$ ]] && ((MAX_MODEL_LEN >= 4096)) || die "invalid QWEN38_VLM_MAX_MODEL_LEN"
  [[ "$MAX_NUM_BATCHED_TOKENS" =~ ^[0-9]+$ ]] && ((MAX_NUM_BATCHED_TOKENS >= 1024)) || die "invalid QWEN38_VLM_MAX_NUM_BATCHED_TOKENS"
  jq -en --arg value "$GPU_MEMORY_UTILIZATION" '($value | tonumber) > 0 and ($value | tonumber) < 1' >/dev/null \
    || die "QWEN38_VLM_GPU_MEMORY_UTILIZATION must be between 0 and 1"
  jq -e '
    (.image | if type == "number" then . > 0 else .count > 0 end)
    and (.video | if type == "number" then . >= 0 else .count >= 0 end)
  ' >/dev/null <<<"$MM_LIMIT" || die "QWEN38_VLM_MM_LIMIT must enable at least one image"
}

validate_model() {
  [[ -d "$MODEL_DIR" ]] || die "model directory not found: $MODEL_DIR"
  [[ -f "$MODEL_DIR/config.json" ]] || die "model config not found: $MODEL_DIR/config.json"
  [[ -f "$MODEL_DIR/preprocessor_config.json" ]] || die "multimodal preprocessor config is missing"
  jq -e '.vision_config and (.architectures | length > 0)' "$MODEL_DIR/config.json" >/dev/null \
    || die "model config does not expose a vision tower"
}

validate_gpu() {
  require_command nvidia-smi
  [[ "$GPUS" =~ ^[0-9]+(,[0-9]+)*$ ]] || die "set QWEN38_VLM_GPUS to explicitly assigned GPU indices"
  local -a gpu_indices=()
  IFS=',' read -r -a gpu_indices <<<"$GPUS"
  local unique_count
  unique_count=$(printf '%s\n' "${gpu_indices[@]}" | sort -u | wc -l)
  ((unique_count == ${#gpu_indices[@]})) || die "QWEN38_VLM_GPUS contains duplicate indices"
  if ((${#gpu_indices[@]} == 1 && MAX_MODEL_LEN > 32768)); then
    die "the default 65K script-planning profile requires two GPUs; lower QWEN38_VLM_MAX_MODEL_LEN for one GPU"
  fi
  TENSOR_PARALLEL_SIZE=${QWEN38_VLM_TENSOR_PARALLEL_SIZE:-${#gpu_indices[@]}}
  [[ "$TENSOR_PARALLEL_SIZE" =~ ^[0-9]+$ ]] || die "invalid QWEN38_VLM_TENSOR_PARALLEL_SIZE"
  ((TENSOR_PARALLEL_SIZE == ${#gpu_indices[@]})) || die "tensor parallel size must equal the assigned GPU count"
  local gpu free_mib
  for gpu in "${gpu_indices[@]}"; do
    free_mib=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits -i "$gpu" 2>/dev/null | tr -d ' ')
    [[ "$free_mib" =~ ^[0-9]+$ ]] || die "GPU index is unavailable: $gpu"
    ((free_mib >= MIN_FREE_MIB)) \
      || die "GPU $gpu has ${free_mib} MiB free; at least ${MIN_FREE_MIB} MiB is required"
  done
}

container_exists() {
  docker inspect "$CONTAINER_NAME" >/dev/null 2>&1
}

container_owned() {
  [[ "$(docker inspect --format '{{index .Config.Labels "io.codex.service"}}' "$CONTAINER_NAME" 2>/dev/null || true)" == "$OWNER_LABEL" ]]
}

container_running() {
  [[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)" == "true" ]]
}

health() {
  curl --noproxy '*' --fail --silent --max-time 5 "http://$HOST:$PORT/health" >/dev/null 2>&1
  curl --noproxy '*' --fail --silent --max-time 5 "http://$HOST:$PORT/v1/models" 2>/dev/null \
    | jq -e --arg model "$MODEL_NAME" '.data | any(.id == $model)' >/dev/null
}

wait_until_ready() {
  local deadline=$((SECONDS + STARTUP_TIMEOUT))
  until health; do
    if ! container_running; then
      docker logs --tail 120 "$CONTAINER_NAME" >&2 || true
      die "Qwen VLM container exited before becoming ready"
    fi
    ((SECONDS < deadline)) || {
      docker logs --tail 120 "$CONTAINER_NAME" >&2 || true
      die "Qwen VLM did not become ready within ${STARTUP_TIMEOUT}s"
    }
    sleep 5
  done
  printf 'ready|base_url=http://%s:%s/v1|model=%s\n' "$HOST" "$PORT" "$MODEL_NAME"
}

vllm_args() {
  printf '%s\0' \
    /model \
    --host 0.0.0.0 \
    --port 8000 \
    --served-model-name "$MODEL_NAME" \
    --dtype bfloat16 \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE:-1}" \
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
    --max-model-len "$MAX_MODEL_LEN" \
    --max-num-seqs 1 \
    --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS" \
    --enable-chunked-prefill \
    --no-enable-prefix-caching \
    --mamba-cache-mode align \
    --limit-mm-per-prompt "$MM_LIMIT" \
    --mm-processor-cache-gb 1 \
    --chat-template /model/chat_template.jinja \
    --chat-template-content-format openai \
    --reasoning-parser qwen3 \
    --default-chat-template-kwargs '{"enable_thinking":true,"preserve_thinking":true,"reasoning_effort":"medium"}' \
    --generation-config vllm \
    --no-enable-log-requests \
    --disable-uvicorn-access-log \
    --trust-remote-code
}

print_command() {
  local -a args=()
  while IFS= read -r -d '' value; do args+=("$value"); done < <(vllm_args)
  printf 'docker run --detach --name %q --label %q --restart unless-stopped --gpus %q --shm-size 16g --publish %q --volume %q --volume %q --volume %q ' \
    "$CONTAINER_NAME" "io.codex.service=$OWNER_LABEL" "\"device=$GPUS\"" "$HOST:$PORT:8000" \
    "$MODEL_DIR:/model:ro" "$CACHE_DIR/vllm:/root/.cache/vllm" "$CACHE_DIR/triton:/root/.triton/cache"
  printf '%q ' "$IMAGE" "${args[@]}"
  printf '\n'
}

start() {
  validate_common
  validate_model
  if container_exists; then
    container_owned || die "container name is owned by another task: $CONTAINER_NAME"
    if container_running; then
      health || die "owned container is running but unhealthy; use restart or inspect logs"
      printf 'already-running|base_url=http://%s:%s/v1|model=%s\n' "$HOST" "$PORT" "$MODEL_NAME"
      return
    fi
    docker rm "$CONTAINER_NAME" >/dev/null
  fi
  validate_gpu
  if command -v ss >/dev/null 2>&1 && ss -ltnH | awk -v address=":$PORT" '$4 ~ address "$" {found=1} END {exit !found}'; then
    die "host port is already in use: $PORT"
  fi
  docker image inspect "$IMAGE" >/dev/null 2>&1 || die "pinned vLLM image is not available locally: $IMAGE"
  mkdir -p "$CACHE_DIR/vllm" "$CACHE_DIR/triton"
  local -a args=()
  while IFS= read -r -d '' value; do args+=("$value"); done < <(vllm_args)
  docker run --detach \
    --name "$CONTAINER_NAME" \
    --label "io.codex.service=$OWNER_LABEL" \
    --label "io.codex.project=novel-manga-video" \
    --restart unless-stopped \
    --gpus "\"device=$GPUS\"" \
    --shm-size 16g \
    --publish "$HOST:$PORT:8000" \
    --volume "$MODEL_DIR:/model:ro" \
    --volume "$CACHE_DIR/vllm:/root/.cache/vllm" \
    --volume "$CACHE_DIR/triton:/root/.triton/cache" \
    "$IMAGE" "${args[@]}" >/dev/null
  printf 'started|container=%s|gpus=%s|port=%s\n' "$CONTAINER_NAME" "$GPUS" "$PORT"
  wait_until_ready
}

stop() {
  validate_common
  if ! container_exists; then
    printf 'not-running|container=%s\n' "$CONTAINER_NAME"
    return
  fi
  container_owned || die "refusing to remove a container owned by another task: $CONTAINER_NAME"
  docker rm --force "$CONTAINER_NAME" >/dev/null
  printf 'stopped|container=%s\n' "$CONTAINER_NAME"
}

status() {
  validate_common
  if ! container_exists; then
    printf 'absent|container=%s\n' "$CONTAINER_NAME"
    return 1
  fi
  container_owned || die "container name is owned by another task: $CONTAINER_NAME"
  if container_running && health; then
    printf 'healthy|base_url=http://%s:%s/v1|model=%s\n' "$HOST" "$PORT" "$MODEL_NAME"
    return
  fi
  printf 'unhealthy|container=%s|state=%s\n' "$CONTAINER_NAME" "$(docker inspect --format '{{.State.Status}}' "$CONTAINER_NAME")"
  return 1
}

smoke() {
  validate_common
  local image_path=${1:-}
  [[ -f "$image_path" ]] || die "usage: $0 smoke /absolute/path/to/image [prompt]"
  health || die "Qwen VLM service is not healthy"
  local prompt=${2:-'只根据图片内容，用一句中文准确描述画面。'}
  local smoke_max_tokens=${QWEN38_VLM_SMOKE_MAX_TOKENS:-3200}
  [[ "$smoke_max_tokens" =~ ^[0-9]+$ ]] || die "invalid QWEN38_VLM_SMOKE_MAX_TOKENS"
  local mime_type
  case "${image_path##*.}" in
    jpg|JPG|jpeg|JPEG) mime_type=image/jpeg ;;
    png|PNG) mime_type=image/png ;;
    webp|WEBP) mime_type=image/webp ;;
    *) die "unsupported image extension: $image_path" ;;
  esac
  jq -nc \
    --arg model "$MODEL_NAME" \
    --arg mime "$mime_type" \
    --arg prompt "$prompt" \
    --argjson max_tokens "$smoke_max_tokens" \
    --rawfile image <(base64 -w0 "$image_path") \
    '{model:$model,temperature:0,max_tokens:$max_tokens,chat_template_kwargs:{enable_thinking:true,preserve_thinking:true,reasoning_effort:"medium"},messages:[{role:"user",content:[{type:"image_url",image_url:{url:("data:"+$mime+";base64,"+$image)}},{type:"text",text:$prompt}]}]}' \
    | curl --noproxy '*' --fail --silent --show-error --max-time 120 \
        -H 'content-type: application/json' \
        --data-binary @- \
        "http://$HOST:$PORT/v1/chat/completions" \
    | jq -er '.choices[0].message.content'
}

usage() {
  cat <<'EOF'
Usage: scripts/qwen38_vlm_service.sh start|stop|restart|status|wait|logs|smoke|print-command

Required for start/restart:
  QWEN38_VLM_GPUS=<two explicitly assigned GPU indices, for example 1,4>

Common overrides:
  QWEN38_VLM_PORT=18120
  QWEN38_VLM_GPU_MEMORY_UTILIZATION=0.62
  QWEN38_VLM_MAX_MODEL_LEN=65536
  QWEN38_VLM_MODEL_DIR=/absolute/model/path
  QWEN38_VLM_CACHE_DIR=/absolute/cache/path
EOF
}

command_name=${1:-}
case "$command_name" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  wait) validate_common; wait_until_ready ;;
  logs) validate_common; docker logs --tail "${QWEN38_VLM_LOG_LINES:-200}" "$CONTAINER_NAME" ;;
  smoke) shift; smoke "$@" ;;
  print-command) validate_common; validate_model; validate_gpu; print_command ;;
  -h|--help|help|'') usage ;;
  *) usage >&2; die "unknown command: $command_name" ;;
esac
