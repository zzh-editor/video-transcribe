#!/usr/bin/env bash
set -euo pipefail

# video-transcribe install script
# Copies skill files to OpenCode/Claude Code skill directory

SOURCE="$(cd "$(dirname "$0")" && pwd)"

# Detect target skill directory
if [ -d "$HOME/.opencode/skills" ]; then
  TARGET="$HOME/.opencode/skills/video-transcribe"
elif [ -d "$HOME/.claude/skills" ]; then
  TARGET="$HOME/.claude/skills/video-transcribe"
else
  echo "Error: no skill directory found at ~/.opencode/skills/ or ~/.claude/skills/"
  echo "Create one of these directories first, then re-run this script."
  exit 1
fi

mkdir -p "$TARGET/scripts"
mkdir -p "$TARGET/docs"

# Copy SKILL.md
cp "$SOURCE/SKILL.md" "$TARGET/SKILL.md"

# Copy scripts
cp "$SOURCE/scripts/transcribe.py" "$TARGET/scripts/transcribe.py"

# Copy docs
cp -r "$SOURCE/docs/"* "$TARGET/docs/"

# Copy config.example.json -> config.json if not exists
if [ ! -f "$TARGET/config.json" ]; then
  cp "$SOURCE/config.example.json" "$TARGET/config.json"
  echo "Created $TARGET/config.json — edit output_dir as needed."
else
  echo "Skipped config.json (already exists)."
fi

echo ""
echo "Installed video-transcribe skill to $TARGET"
echo ""
echo "Usage: restart your AI coding tool, then say:"
echo "  转录视频 /path/to/video.mp4"
echo "  transcribe video /path/to/video.mp4"
