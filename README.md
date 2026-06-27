## QMK Raw HID Shared Keys Host

Windows host application for passing shared key messages between QMK devices over raw HID.

Also communicates with running instances of Neovim either natively, or in WSL, and can use current Neovim mode to pass flags to QMK devices, enabling things like context-aware layers.

See example device implementations in Cantor and Adept at: https://github.com/windexlight/qmk_userspace/tree/main/keyboards

#### Dependencies:
```
python -m pip install hidapi
python -m pip install pywin32
python -m pip install psutil
python -m pip install msgpack
```

#### To set up as a task that runs at logon and remains running until shutdown:

- Open Windows Task Scheduler
- Actions -> Create Task...
- General -> Name -> "QMK Shared Keys Host", or whatever you like
- General -> Security options -> Run only when user is logged on
- Triggers -> New...
- New Trigger -> Begin the task -> At log on
- New Trigger -> Settings -> Specific user -> Your account
- New Trigger -> OK
- Actions -> New...
- New Action -> Action -> Start a program
- New Action -> Settings -> Program/script -> Absolute path to pythonw.exe
- New Action -> Settings -> Add arguments (optional) -> shared_keys_host.py
- New Action -> Settings -> Start in (optional) -> Absolute path to folder contaning shared_keys_host.py
- New Action -> OK
- Conditions -> Power -> Stop if the computer switches to battery power -> Uncheck
- Conditions -> Power -> Start the task only if the computer it on AC power -> Uncheck
- Conditions -> Idle -> Stop if the computer ceases to be idle -> Uncheck
  - Note: this is probably not necessery, and unchecking it requires briefly checking "Start the task only if the computer is idle for" (which defaults to unchecked) to enable "Stop if the computer ceases to be idle" in order to uncheck it, then once again unchecking "Start the task only if the computer is idle for".
- Settings -> Stop the task if it runs longer than -> Uncheck

Note that this is designed around a single-user system, and will likely not play well on a system where multiple users log in simultaneoutly and all want to run this same script.
