#!/bin/bash
# Stops the Job Scout background service.
# Removes the old login-agent (if any) and kills the running server.
PLIST="$HOME/Library/LaunchAgents/com.jobscout.browser.plist"
launchctl unload "$PLIST" 2>/dev/null
rm -f "$PLIST" 2>/dev/null
pkill -f "job_scout_browser.py" 2>/dev/null

echo ""
if pgrep -f "job_scout_browser.py" >/dev/null 2>&1; then
  echo "Tried to stop Job Scout but a process is still running. Try again, or restart your Mac."
else
  echo "Job Scout background service stopped."
fi
echo ""
read -n 1 -s -r -p "Press any key to close."
