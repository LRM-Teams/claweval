#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target_image="${PI_TARGET_IMAGE:-pi-agent-wildclawbench-1.0:latest}"
settings_file="${PI_SETTINGS_FILE:-/home/ganxin2/.pi/agent/settings.json}"
base_alias="pi-agent-wildclawbench-base:0.84.2"
if [[ ! -f "$settings_file" ]]; then
  printf 'Pi settings file not found: %s\n' "$settings_file" >&2
  exit 1
fi
if [[ -n "${PI_BASE_IMAGE:-}" ]]; then
  base_image="$PI_BASE_IMAGE"
elif docker image inspect "$base_alias" >/dev/null 2>&1; then
  base_image="$base_alias"
else
  base_image="$target_image"
fi
expected_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_image_id"])' "$root/image.lock.json")"
actual_id="$(docker image inspect --format '{{.Id}}' "$base_image")"
if [[ "$actual_id" != "$expected_id" && "${ALLOW_PI_BASE_MISMATCH:-0}" != "1" ]]; then
  printf 'Pi base image mismatch: expected %s, got %s\n' "$expected_id" "$actual_id" >&2
  exit 1
fi
if [[ "$base_image" == "$target_image" ]]; then
  docker tag "$base_image" "$base_alias"
  base_image="$base_alias"
fi
docker build --platform linux/amd64 --build-arg "PI_BASE_IMAGE=$base_image" --secret "id=pi_settings,src=$settings_file" -t "$target_image" "$root"
