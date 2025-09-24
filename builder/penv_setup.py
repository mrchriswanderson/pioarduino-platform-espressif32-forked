# Copyright 2014-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from platformio.compat import IS_WINDOWS

python_deps = {
    "platformio": "https://github.com/pioarduino/platformio-core/archive/refs/tags/v6.1.18.zip",
    "pyyaml": ">=6.0.2",
    "rich-click": ">=1.8.6",
    "zopfli": ">=0.2.2",
    "intelhex": ">=2.3.0",
    "rich": ">=14.0.0",
    "urllib3": "<2",
    "cryptography": ">=45.0.3",
    "certifi": ">=2025.8.3",
    "ecdsa": ">=0.19.1",
    "bitstring": ">=4.3.1",
    "reedsolo": ">=1.5.3,<1.8",
    "esp-idf-size": ">=1.6.1"
}

def has_internet_connection(host="1.1.1.1", port=53, timeout=2):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def get_executable_path(venv_dir, executable_name):
    exe_suffix = ".exe" if IS_WINDOWS else ""
    scripts_dir = "Scripts" if IS_WINDOWS else "bin"
    return str(Path(venv_dir) / scripts_dir / f"{executable_name}{exe_suffix}")

def create_temp_venv(python_executable):
    temp_dir = tempfile.mkdtemp(prefix="penv_temp_")
    uv_cmd = "uv"
    try:
        subprocess.check_call([
            uv_cmd, "venv",
            "--clear",
            f"--python={python_executable}",
            temp_dir
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        print(f"[STEP 1] Created temporary uv venv at {temp_dir}")
    except Exception as e:
        print(f"Error creating temp uv venv: {e}", file=sys.stderr)
        sys.exit(1)
    return temp_dir

def launch_temp_venv(temp_dir, penv_dir, script_path, extra_args):
    temp_python = Path(temp_dir) / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
    if not temp_python.exists():
        print(f"Temp python executable not found: {temp_python}", file=sys.stderr)
        sys.exit(1)
    args = [str(temp_python), script_path, "--in-temp", penv_dir, temp_dir] + extra_args
    subprocess.Popen(args, close_fds=True)
    sys.exit(0)

def ensure_pip(python_executable):
    try:
        subprocess.check_call([python_executable, "-m", "pip", "--version"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        print("Pip is not available, try to install with ensurepip.")
        try:
            subprocess.check_call([python_executable, "-m", "ensurepip", "--default-pip"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            return False


def install_dependencies(python_executable):
    if not ensure_pip(python_executable):
        print(f"Failed to install pip: {e}", file=sys.stderr)
        return False

    penv_dir = Path(python_executable).parent.parent
    uv_exec = get_executable_path(penv_dir, "uv")

    uv_in_penv_available = False
    try:
        result = subprocess.run([uv_executable, "--version"], capture_output=True, text=True)
        uv_in_penv_available = result.returncode == 0
    except Exception:
        uv_in_penv_available = False

    if not uv_in_penv_available:
        try:
            subprocess.check_call([python_executable, "-m", "pip", "install", "uv>=0.1.0", "--quiet"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
            print("uv erfolgreich installiert.")
        except subprocess.CalledProcessError as e:
            print(f"Error installing uv package: {e}", file=sys.stderr)
            return False

    try:
        res = subprocess.run([uv_exec, "pip", "list", "--format=json", f"--python={python_executable}"],
                             capture_output=True, text=True, timeout=300)
        if res.returncode == 0:
            installed = {p["name"].lower(): p["version"] for p in json.loads(res.stdout)}
        else:
            installed = {}
    except Exception as e:
        print(f"Warning: could not list installed packages: {e}", file=sys.stderr)
        installed = {}

    to_install = []
    for pkg, ver_req in python_deps.items():
        if pkg.lower() not in installed:
            if ver_req.startswith(("http://", "https://", "git+", "file://")):
                to_install.append(ver_req)
            else:
                to_install.append(f"{pkg}{ver_req}")

    if to_install:
        try:
            subprocess.check_call([uv_exec, "pip", "install", "--quiet", "--upgrade", f"--python={python_executable}"] + to_install,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
            print(f"Installed/updated packages: {to_install}")
        except Exception as e:
            print(f"Error installing dependencies: {e}", file=sys.stderr)
            return False
    return True

def write_marker(penv_dir):
    marker = Path(penv_dir) / "pioarduino_py"
    marker.write_text("required by pioarduino\n")
    print("[STEP 6] Marker file written.")

def launch_penv_python(penv_dir, args):
    python_bin = Path(penv_dir) / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
    if not python_bin.exists():
        print(f"Error: Penv python not found: {python_bin}", file=sys.stderr)
        sys.exit(1)
    subprocess.call([str(python_bin)] + args)

def in_temp_process(penv_dir, temp_dir, script_path, args):
    penv_path = Path(penv_dir)

    if penv_path.exists():
        shutil.rmtree(penv_path)

    try:
        subprocess.check_call([
            "uv", "venv", "--clear",
            f"--python={sys.executable}", penv_dir
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        print(f"[STEP 5] Created final uv venv in {penv_dir}")
    except Exception as e:
        print(f"Failed creating final venv: {e}", file=sys.stderr)
        sys.exit(1)

    write_marker(penv_dir)
    
    if has_internet_connection():
        if not install_dependencies(str(penv_path / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python"))):
            print("Failed to install Python dependencies", file=sys.stderr)
            sys.exit(1)
    else:
        print("No internet - skipping dependency installation")

    launch_penv_python(penv_dir, args)
    sys.exit(0)

def setup_pipenv(env, penv_dir):
    if not Path(get_executable_path(penv_dir, "python")).exists():
        uv_exe = None
        try:
            python_exe = env.subst("$PYTHONEXE")
            uv_exe = Path(python_exe).parent / ("uv.exe" if IS_WINDOWS else "uv")
            if not uv_exe.exists():
                uv_exe = "uv"
            subprocess.check_call([str(uv_exe), "venv", "--clear", f"--python={python_exe}", penv_dir],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            write_marker(penv_dir)
            return str(uv_exe) if uv_exe != "uv" else None
        except Exception:
            pass
        env.Execute(env.VerboseAction(f'"$PYTHONEXE" -m venv --clear "{penv_dir}"',
                                      f"Creating Python virtual environment at {penv_dir}"))
        write_marker(penv_dir)
    return None

def setup_python_path(penv_dir):
    python_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_path = Path(penv_dir) / ("Lib" if IS_WINDOWS else "lib") / python_ver / "site-packages"
    if site_path.exists():
        import site
        site.addsitedir(str(site_path))

def _setup_python_environment_core(env, platform, platformio_dir, should_install_esptool=True):
    penv_dir = str(Path(platformio_dir) / "penv")

    if "--in-temp" in sys.argv:
        idx = sys.argv.index("--in-temp")
        penv_dir_arg = sys.argv[idx + 1]
        temp_dir_arg = sys.argv[idx + 2]
        rest_args = sys.argv[idx + 3:]
        in_temp_process(penv_dir_arg, temp_dir_arg, str(Path(__file__).absolute()), rest_args)

    uv_exe = None
    if env:
        uv_exe = setup_pipenv(env, penv_dir)
    else:
        uv_exe = _setup_pipenv_minimal(penv_dir)

    penv_python = get_executable_path(penv_dir, "python")
    if env:
        env.Replace(PYTHONEXE=penv_python)

    if not Path(penv_python).exists():
        print(f"Python executable not found at {penv_python}", file=sys.stderr)
        sys.exit(1)

    setup_python_path(penv_dir)

    uv_bin = get_executable_path(penv_dir, "uv")
    esptool_bin = get_executable_path(penv_dir, "esptool")

    if has_internet_connection() or os.getenv("GITHUB_ACTIONS"):
        if not install_dependencies(penv_python):
            print("Failed to install Python dependencies", file=sys.stderr)
            sys.exit(1)

    if should_install_esptool:
        if env:
            # Implement install esptool logic for env
            pass
        else:
            # Implement fallback esptool install
            pass

    # Setup certificate environment variables if needed

    return penv_python, esptool_bin

def _setup_pipenv_minimal(penv_dir):
    if not Path(get_executable_path(penv_dir, "python")).exists():
        python_exe = sys.executable
        try:
            uv_exe_guess = Path(python_exe).parent / ("uv.exe" if IS_WINDOWS else "uv")
            uv_exe = str(uv_exe_guess) if uv_exe_guess.exists() else "uv"
            subprocess.check_call([uv_exe, "venv", "--clear", f"--python={python_exe}", penv_dir], timeout=90)
            write_marker(penv_dir)
            return uv_exe
        except Exception:
            subprocess.check_call([python_exe, "-m", "venv", "--clear", penv_dir])
            write_marker(penv_dir)
            return None
    return None
