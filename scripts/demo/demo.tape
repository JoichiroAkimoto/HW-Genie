# HW-Genie README demo GIF source.
# Re-records docs/assets/hw-genie-demo.gif from canned output (no real API calls).
#
# Usage: 必ずリポジトリルートから実行すること（相対パスがルート基準のため）。
#   vhs scripts/demo/demo.tape
#
# Requires:
#   - vhs 0.11.0+ (`brew install vhs` — 依存で ffmpeg/ttyd も導入)
#   - bash 3.2+ / sleep (POSIX)
#   - 再生成後は docs/assets/hw-genie-demo.gif をコミット
#
# Notes:
#   - 実API/DBは叩かない（scripts/demo/mock_uv.sh がダミー出力を再生）。
#   - フォントは Menlo を優先し無ければ JetBrains Mono / monospace にフォールバック。
#   - Sleep 値は mock 内部の say 合計(~10.9s) + TypingSpeed を考慮して設定。
#   - --iterations 等の実行例は本GIFでは簡潔さのため daily の固定出力に絞っている。

Output docs/assets/hw-genie-demo.gif

Set Shell bash
Set FontFamily "Menlo, JetBrains Mono, monospace"
Set FontSize 17
Set Width 1200
Set Height 700
Set Framerate 20
Set Theme Dracula
Set Padding 24
Set Margin 32
Set MarginFill "#2b2158"
Set WindowBar Rings
Set BorderRadius 12
Set TypingSpeed 60ms
Set CursorBlink false

Hide
Type 'MOCK_BIN_DIR=$(mktemp -d /tmp/hwg-vhs-XXXXXX) && cp "$PWD/scripts/demo/mock_uv.sh" "$MOCK_BIN_DIR/uv" && chmod +x "$MOCK_BIN_DIR/uv" && export PATH="$MOCK_BIN_DIR:$PATH"'
Enter
Type 'trap "rm -rf $MOCK_BIN_DIR" EXIT'
Enter
Type "export PS1='\[\e[32m\]➜ \[\e[36m\]~/dev/HW-Genie\[\e[0m\] $ '"
Enter
Type 'clear'
Enter
Show

Type "uv run hw-genie auth --list"
Enter
Sleep 5500ms

Type 'clear'
Enter

Type "uv run hw-genie daily --account Arthur"
Enter
Sleep 15500ms

Sleep 4000ms

Hide
Type 'rm -rf "$MOCK_BIN_DIR" 2>/dev/null; clear'
Enter
Show
