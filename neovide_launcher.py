import os
import subprocess
import uuid
import time
import argparse
import re

def launch_neovide(args, argv):
    launch_id = str(uuid.uuid4())
    child_env = os.environ.copy()
    child_env["NVIM_LAUNCH_ID"] = launch_id
    child_env["WSLENV"] = "NVIM_LAUNCH_ID/u"

    proc = subprocess.Popen([re.sub(r"\$\$nvim", "$nvim", re.sub(r"(?<!\$)\$nvim", args.nvim, x)) for x in argv],
        env=child_env,
        cwd=args.cwd or None,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_BREAKAWAY_FROM_JOB,
    )
    handshake_sock = f"/tmp/nvim-handshake-{launch_id}.sock"
    rpc_command = f"'v:lua.ReceiveWindowsPid({proc.pid})'"

    max_retries = 25
    for _ in range(max_retries):
        time.sleep(0.2)
        try:
            result = subprocess.run(
                ["wsl.exe", args.nvim, "--headless", "--server", handshake_sock, "--remote-expr", rpc_command],
                capture_output=True,
                text=True
            )
            if "HANDSHAKE_COMPLETE" in result.stdout:
                return
        except Exception as e:
            print(e)
            pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wrapper for launching WSL nvim with QMK Shared Keys Host compatibility")
    parser.add_argument("--cwd", type=str, default="", help="Windows path to the WSL directory you want nvim to use as its starting cwd. Example: \\\\wsl$\\[distro]\\home\\[username]")
    parser.add_argument("--nvim", type=str, default="nvim", help="WSL path to nvim binary you wish to use, if you do not have the one you want on the WSL path. May be referenced with $nvim as later argument (use $$nvim for literal $nvim), though note that you will need to escape the $ character, depending on the shell (for example, use `$nvim in PowerShell).")
    # Remaining args should be the command to launch nvim within WSL, such as "neovide.exe --wsl"
    args, argv = parser.parse_known_args()
    launch_neovide(args, argv)
