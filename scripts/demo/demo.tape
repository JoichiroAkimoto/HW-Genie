# HW-Genie README demo GIF source.
# Re-records docs/assets/hw-genie-demo.gif from canned output (no real API calls).
#
# Usage: 必ずリポジトリルートから実行すること（相対パスがルート基準のため）。
#   vhs scripts/demo/demo.tape
#
# Requires:
#   - vhs 0.11.0+ (`brew install vhs` — 依存で ffmpeg/ttyd も導入)
#   - zsh + zsh-autosuggestions (補完) のみを読み込み（ユーザ環境は読まない）
#   - gifsicle (`brew install gifsicle`) — 生成後の圧縮に使用
#   - 再生成後は docs/assets/hw-genie-demo.gif をコミット（自動で gifsicle 圧縮）
#
# Notes:
#   - 実API/DBは叩かない（scripts/demo/mock_uv.sh がダミー出力を再生）。
#   - フォントは Menlo を優先し無ければ JetBrains Mono / monospace にフォールバック。
#   - Sleep 値は mock 内部の say 合計(~13s) + TypingSpeed を考慮して設定。
#   - --iterations 等の実行例は本GIFでは簡潔さのため daily の固定出力に絞っている。
#   - 圧縮: vhs 生成後に `gifsicle -O3 --lossy=30 --colors 128` で約 30-40% 削減

Output docs/assets/hw-genie-demo.gif

Set Shell zsh
Set FontFamily "Menlo, JetBrains Mono, monospace"
Set FontSize 16
Set Width 1200
Set Height 1050
Set Framerate 12
Set Theme Dracula
Set Padding 24
Set Margin 32
Set MarginFill "#2b2158"
Set WindowBar Rings
Set BorderRadius 12
Set TypingSpeed 60ms
Set CursorBlink false

Hide
Type 'MOCK_BIN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/hwg-vhs-XXXXXX") || { echo "mock setup failed" >&2; exit 1; } && cp "$PWD/scripts/demo/mock_uv.sh" "$MOCK_BIN_DIR/uv" && chmod +x "$MOCK_BIN_DIR/uv" && export PATH="$MOCK_BIN_DIR:$PATH"'
Enter
Type 'trap "rm -rf $MOCK_BIN_DIR" EXIT'
Enter
Type 'if command -v brew >/dev/null 2>&1 && [[ -f "$(brew --prefix 2>/dev/null)/share/zsh-autosuggestions/zsh-autosuggestions.zsh" ]]; then source "$(brew --prefix 2>/dev/null)/share/zsh-autosuggestions/zsh-autosuggestions.zsh"; elif [[ -f /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh ]]; then source /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh; elif [[ -f /usr/local/share/zsh-autosuggestions/zsh-autosuggestions.zsh ]]; then source /usr/local/share/zsh-autosuggestions/zsh-autosuggestions.zsh; fi'
Enter
Type 'export PS1="%F{green}➜%f %F{cyan}~/dev/HW-Genie%f $ "'
Enter
Type 'HISTFILE="$MOCK_BIN_DIR/.zsh_history" && touch "$HISTFILE"'
Enter
Type 'print -s "uv run hw-genie auth --list"'
Enter
Type 'print -s "uv run hw-genie daily --account Arthur"'
Enter
Type 'print -s "uv run hw-genie multi full --help"'
Enter
Type 'clear'
Enter
Show

Type "uv run hw-genie auth --list"
Enter
Sleep 5500ms

Enter
Sleep 300ms
Enter
Sleep 300ms
Enter
Sleep 800ms

Type "uv run hw-genie daily --account Arthur"
Enter
Sleep 16500ms

Sleep 2000ms

# 匂わせ: 次に multi full を実行しそうなところで止める
Type "uv run hw-genie multi full"
Sleep 1200ms

Sleep 1500ms

Hide
Type 'rm -rf "$MOCK_BIN_DIR" 2>/dev/null'
Enter
