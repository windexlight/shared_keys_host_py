import os
import subprocess
import uuid
import time

def launch_neovide():
    launch_id = str(uuid.uuid4())
    child_env = os.environ.copy()
    child_env["NVIM_LAUNCH_ID"] = launch_id
    child_env["WSLENV"] = "NVIM_LAUNCH_ID/u"

    # TODO: pass all this in from outside
    proc = subprocess.Popen([r"C:\Program Files\Neovide\neovide.exe", "--wsl", "--neovim-bin", "~/.local/share/bob/nvim-bin/nvim", "--log"],
        env=child_env,
        cwd=r"\\wsl$\Ubuntu\home\windexlight",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_BREAKAWAY_FROM_JOB,
    )
    print(f"Launched Neovide (PID: {proc.pid}). Waiting for Neovim to initialize...")

    handshake_sock = f"/tmp/nvim-handshake-{launch_id}.sock"
    rpc_command = f"'v:lua.ReceiveWindowsPid({proc.pid})'"

    max_retries = 25
    for _ in range(max_retries):
        time.sleep(0.2)
        try:
            result = subprocess.run(
                ["wsl.exe", "nvim", "--headless", "--server", handshake_sock, "--remote-expr", rpc_command],
                capture_output=True,
                text=True
            )
            if "HANDSHAKE_COMPLETE" in result.stdout:
                print(f"Success! Handshake complete. RPC is now bound to /tmp/nvim-win-{proc.pid}.sock")
                return
            else:
                print(result.stdout, result.stderr)
        except Exception as e:
            print(e)

    print("Error: Timed out waiting for Neovim handshake.")
    pass

if __name__ == "__main__":
    launch_neovide()
    pass
