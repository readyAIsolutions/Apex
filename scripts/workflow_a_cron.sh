#!/usr/bin/env bash
# APEX Workflow A weekly demand research (cron). Runs venv python on this box.
# Replace the 'demand.pull' provider_routes row with gsc/dataforseo when credentialed.
cd /home/nova/apex || exit 1
exec .venv/bin/python scripts/workflow_a.py 2>&1