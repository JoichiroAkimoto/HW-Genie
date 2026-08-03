#!/usr/bin/env bash
set -euo pipefail

extract_metadata_block() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    echo "ERROR: file not found: $file" >&2
    exit 1
  fi
  local block
  block=$(sed -n '/^\/\/ ==UserScript==$/,/^\/\/ ==\/UserScript==$/p' "$file")
  if [[ -z "$block" || "$block" != *"==UserScript=="* ]]; then
    echo "ERROR: metadata block not found in $file" >&2
    exit 1
  fi
  printf '%s\n' "$block"
}

get_version() {
  local source_file="$1"
  local block
  block=$(extract_metadata_block "$source_file")

  local version_lines
  version_lines=$(grep -E '^\/\/ @version[[:space:]]+' <<< "$block" || true)

  if [[ -z "$version_lines" ]]; then
    echo "ERROR: @version line not found in $source_file" >&2
    exit 1
  fi

  local count
  count=$(wc -l <<< "$version_lines" | tr -d ' ')
  if [[ "$count" -ne 1 ]]; then
    echo "ERROR: multiple @version lines found in $source_file" >&2
    exit 1
  fi

  local version
  version=$(sed -n 's/^\/\/ @version[[:space:]]*//p' <<< "$version_lines" | tr -d ' \r\n')

  if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: version '$version' is not a valid semver (X.Y.Z) in $source_file" >&2
    exit 1
  fi

  printf '%s\n' "$version"
}

cmd_version() {
  if [[ $# -lt 1 ]]; then
    echo "Usage: release-metadata.sh version <source-index.ts>" >&2
    exit 1
  fi
  get_version "$1"
}

cmd_validate_tag() {
  if [[ $# -lt 2 ]]; then
    echo "Usage: release-metadata.sh validate-tag <source-index.ts> <tag>" >&2
    exit 1
  fi
  local source_file="$1"
  local tag="$2"

  local version
  version=$(get_version "$source_file")

  if [[ "v$version" != "$tag" ]]; then
    echo "ERROR: tag '$tag' does not match @version v$version in $source_file" >&2
    exit 1
  fi
}

cmd_validate_artifact() {
  if [[ $# -lt 3 ]]; then
    echo "Usage: release-metadata.sh validate-artifact <artifact.user.js> <version> <owner/repo>" >&2
    exit 1
  fi
  local artifact_file="$1"
  local expected_version="$2"
  local owner_repo="$3"

  local block
  block=$(extract_metadata_block "$artifact_file")

  if grep -qE '__[A-Z0-9_]+__' <<< "$block"; then
    echo "ERROR: unresolved placeholders found in $artifact_file" >&2
    exit 1
  fi

  local actual_version
  actual_version=$(sed -n 's/^\/\/ @version[[:space:]]*//p' <<< "$block" | head -n 1 | tr -d ' \r\n')
  if [[ "$actual_version" != "$expected_version" ]]; then
    echo "ERROR: version mismatch in $artifact_file (expected '$expected_version', got '$actual_version')" >&2
    exit 1
  fi

  local expected_download_url="https://github.com/${owner_repo}/releases/download/v${expected_version}/hw-genie-auth-capture.user.js"
  local actual_download_url
  actual_download_url=$(sed -n 's/^\/\/ @downloadURL[[:space:]]*//p' <<< "$block" | head -n 1 | tr -d ' \r\n')
  if [[ "$actual_download_url" != "$expected_download_url" ]]; then
    echo "ERROR: downloadURL mismatch in $artifact_file (expected '$expected_download_url', got '$actual_download_url')" >&2
    exit 1
  fi

  local expected_update_url="https://github.com/${owner_repo}/releases/latest/download/hw-genie-auth-capture.user.js"
  local actual_update_url
  actual_update_url=$(sed -n 's/^\/\/ @updateURL[[:space:]]*//p' <<< "$block" | head -n 1 | tr -d ' \r\n')
  if [[ "$actual_update_url" != "$expected_update_url" ]]; then
    echo "ERROR: updateURL mismatch in $artifact_file (expected '$expected_update_url', got '$actual_update_url')" >&2
    exit 1
  fi
}

main() {
  if [[ $# -lt 1 ]]; then
    echo "Usage: release-metadata.sh <version|validate-tag|validate-artifact> [args...]" >&2
    exit 1
  fi

  local subcommand="$1"
  shift

  case "$subcommand" in
    version)
      cmd_version "$@"
      ;;
    validate-tag)
      cmd_validate_tag "$@"
      ;;
    validate-artifact)
      cmd_validate_artifact "$@"
      ;;
    *)
      echo "ERROR: unknown subcommand '$subcommand'" >&2
      exit 1
      ;;
  esac
}

main "$@"
