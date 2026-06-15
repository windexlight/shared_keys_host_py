import pynvim

WSL_SOCKET_PATH = "/tmp/nvim-win-25056.sock"

def on_notification(method, args):
    if method == "mode_change":
        print(f"[Windows] Mode changed to: {args}")

def main():
    print(f"Spawning WSL bridge to {WSL_SOCKET_PATH}...")

    command = ['wsl.exe', '-e', 'nc', '-U', WSL_SOCKET_PATH] # relies on netcat being available in WSL

    try:
        nvim = pynvim.attach('child', argv=command)
        print("Connected successfully via standard pipes! Listening...")
        while True:
            msg = nvim.next_message()
            print(msg)
        # nvim.run_loop(None, on_notification)

    except Exception as e:
        print(f"Failed to connect. Is the WSL socket path correct? Error: {e}")

if __name__ == "__main__":
    main()