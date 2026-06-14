import os
import socket
import subprocess

def launch_neovide():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(('127.0.0.1', 0))
    server_socket.listen(1)

    assigned_port = server_socket.getsockname()[1]

    child_env = os.environ.copy()
    child_env["NVIM_HANDSHAKE_PORT"] = str(assigned_port)
    child_env["WSLENV"] = "NVIM_HANDSHAKE_PORT/u"

    # TODO: pass all this in from outside
    proc = subprocess.Popen([r"C:\Program Files\Neovide\neovide.exe", "--wsl", "--neovim-bin", "~/.local/share/bob/nvim-bin/nvim", "--log"], env=child_env) #, cwd=r"\\wsl$\Ubuntu\home\windexlight")
    print(f"[Python] Launched Neovide with PID: {proc.pid}")
    print(f"[Python] Listening for Neovim connection on port {assigned_port}...")

    server_socket.settimeout(5.0)

    try:
        client_socket, client_address = server_socket.accept()
        print(f"[Python] Neovim connected from {client_address}")

        request = client_socket.recv(1024).decode('utf-8')

        if "GET_PID" in request:
            response = f"{proc.pid}\n"
            client_socket.sendall(response.encode('utf-8'))
            print(f"[Python] Sent PID {proc.pid} to Neovim. Handshake complete.")

        client_socket.close()
    except socket.timeout:
        print("[Error] Handshake timed out. Neovim did not connect within 5 seconds.")
    finally:
        server_socket.close()

if __name__ == "__main__":
    launch_neovide()
    pass
