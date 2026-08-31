#!/bin/bash
# Stamp the start of the night window into the logs the overnight report
# scopes to. patrol.log timestamps are HH:MM:SS with no date, so the report
# cannot reliably cut "since 21:00 yesterday" by parsing times -- before this
# marker existed it grepped ALL-TIME lines into a nightly report, and on
# 2026-08-31 that put three previous nights' deterrent lines into the morning
# read of a night that had one fire. Both logs are opened in append mode
# (shell >> redirect for the patrol, systemd append: for the detector), so an
# external append cannot clobber anything.
STAMP="===== NIGHT WINDOW START $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "$STAMP" >> /home/david/ocp-watch/patrol.log
echo "$STAMP" >> /home/david/projects/ocp/frames/server.log
