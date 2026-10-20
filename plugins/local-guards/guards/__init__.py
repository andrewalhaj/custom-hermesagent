"""Native-hook guards. Each public module exports HOOKS = {hook_name: callback}.

The local-guards loader registers every HOOKS entry via ctx.register_hook; modules
starting with "_" (shared helpers) are skipped.
"""
