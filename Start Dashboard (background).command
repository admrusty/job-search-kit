#!/bin/bash
# Starts the job dashboard in the background.
# It inherits this Terminal's permission to read your Documents folder, then
# keeps running after this window closes (until you log out, restart, or stop it).
cd "$(dirname "$0")"
DIR="$(pwd)"
PY="$(command -v python3)"

# Remove the old login-agent approach if it was installed — macOS privacy rules
# block a headless agent from reading ~/Documents, so we don't use it.
PLIST="$HOME/Library/LaunchAgents/com.jobscout.browser.plist"
launchctl unload "$PLIST" 2>/dev/null
rm -f "$PLIST" 2>/dev/null

if [ -z "$PY" ]; then
  echo "Could not find python3."
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi

# Always stop any running instance first, so the LATEST code is loaded.
pkill -f "job_scout_browser.py" 2>/dev/null
sleep 1
: > "$DIR/job-scout-service.log"
nohup "$PY" "$DIR/job_scout_browser.py" --no-open >> "$DIR/job-scout-service.log" 2>&1 &
disown
sleep 2

# Confirm it actually came up before declaring success.
if pgrep -f "job_scout_browser.py" >/dev/null 2>&1; then
  osascript -e 'tell application "Terminal" to set close on shell exit of selected tab of front window to true' 2>/dev/null
  # Open the dashboard in the user's DEFAULT browser.
  open "http://127.0.0.1:8733/"
else
  echo ""
  echo "The dashboard did not start. Last log lines:"
  tail -n 5 "$DIR/job-scout-service.log"
  echo ""
  read -n 1 -s -r -p "Press any key to close."
fi
