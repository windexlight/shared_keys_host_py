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
