Windows Task Scheduler Setup — JAL Flight Tracker

The task runs once a day at 1:00 AM and kicks off `run-tracker.bat`, which calls `claude -p` to execute the workflow described in `CLAUDE.md`.

## Creating / recreating the task (command line)

Run from any shell that can invoke `schtasks` (Git Bash, PowerShell, cmd). `//` escapes are for Git Bash; in cmd/PowerShell use single `/`.

```
schtasks //create //tn "JAL Flight Tracker" //tr "C:\dev\jal-flights-tracker\run-tracker.bat" //sc DAILY //st 01:00 //sd 04/15/2026 //f
```

`/f` overwrites an existing task with the same name, so this command is idempotent.

## Verifying

```
schtasks //query //tn "JAL Flight Tracker" //v //fo LIST
```

Expect `Next Run Time` to be tomorrow at 01:00 and `Status` to be `Ready`.

## Running on demand

```
schtasks //run //tn "JAL Flight Tracker"
```

Then `tail -f tracker-log.txt` in the project dir to watch output.

## Deleting

```
schtasks //delete //tn "JAL Flight Tracker" //f
```

## Editing in the GUI

`taskschd.msc` → Task Scheduler Library → `JAL Flight Tracker`. Use the GUI for less-common tweaks:

- **Conditions** tab → uncheck "Start the task only if the computer is on AC power" (if running on a laptop).
- **Settings** tab → check "Run task as soon as possible after a scheduled start is missed" (recommended — catches missed runs when the machine was off).
- **Triggers** tab → once we've identified JAL's exact 360-day drop time, change the trigger to fire a few minutes after that moment.

## Troubleshooting

- `Last Result` of `0x0` = success. Anything else: inspect `tracker-log.txt` for the claude session output.
- "Interactive only" logon mode means the task only fires when the user is logged in. Leave on unless you need unattended-login runs (which would require storing credentials in Task Scheduler — avoid).
- If `claude` isn't found when the task runs, add `set PATH=%PATH%;C:\Users\thunderhead\AppData\Local\...` to `run-tracker.bat` before the `claude -p` line.
