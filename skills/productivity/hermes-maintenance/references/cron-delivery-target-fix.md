# Cron job delivering to wrong channel — fix pattern

## Symptom
A cron job's output appears in the current chat session instead of the intended
channel (e.g. Telegram Cron Jobs channel). This happens when a cron was created
or updated from inside a specific session and `deliver: "origin"` anchors it to
that session's DM/channel.

## Root cause
`deliver: "origin"` (the default) resolves the delivery target at job creation
time and locks it to that session's platform + chat_id + thread_id. When the job
was created from a Telegram DM, `origin` = that DM. Moving to a dedicated channel
requires explicitly setting a target.

## Fix

1. **List available targets** to get the exact target string:
   ```
   send_message(action='list')
   ```
   Look for the intended target, e.g. `telegram:Cron Jobs (channel)`.

2. **Update the job** with the explicit target:
   ```
   cronjob(action='update', job_id='<id>', deliver='telegram:Cron Jobs (channel)')
   ```
   Use `cronjob(action='list')` first if you don't have the job_id.

3. **Verify** the `deliver` field in the returned job object.

## Notes
- `deliver: "local"` = save only, no delivery (for silent/no-alert crons).
- `deliver: "all"` = fan out to every connected home channel.
- For Discord threads: `discord:#channel-name` or `discord:<channel_id>:<thread_id>`.
- The Telegram Cron Jobs channel ID is `<telegram-chat-id>` (also addressable as
  `telegram:Cron Jobs (channel)` via the target list).
- This is a non-gated update — `cronjob(action='update')` does not require greenlight.
