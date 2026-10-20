#!/usr/bin/env python3
"""Cron entry point (the script field takes no args); the check lives in skill_desc_reconcile.py."""
from skill_desc_reconcile import main

main(["--check"])
