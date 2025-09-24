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
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from shutil import rmtree

from platformio.compat import IS_WINDOWS
from platformio.package.version import pepver_to_semver

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
    script_dir = "Scripts" if IS_WINDOWS else "bin"
    return str(Path(venv_dir) / script_dir / f"{executable_name}{exe_suffix}")

def create_temp_venv(python_executable):
    temp_dir = tempfile.mkdtemp(prefix="penv_temp_")
    try:
        subprocess.check_call(
            ["uv", "venv", "--clear", f"--python={python_executable}", temp_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        print(f"[Step 1] Created temp venv: {temp_dir}")
    except Exception as e:
        print(f"Failed to create temp venv: {e}", file=sys.stderr)
        sys.exit(1)
    return temp_dir

def launch_temp_venv(temp_dir, penv_dir, script_path, args):
    python_bin = Path(temp_dir) / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
    if not python_bin.exists():
        print(f"Temporary python not found: {python_bin}", file=sys.stderr)
        sys.exit(1)
    cmd = [str(python_bin), script_path, "--in-temp", penv_dir, temp_dir] + args
    print(f"[Step 3] Launching temp python process: {' '.join(cmd)}")
    subprocess.Popen(cmd, close_fds=True)
    sys.exit(0)

def in_temp_process(penv_dir, temp_dir, script_path, args):
    penv_path = Path(penv_dir)
    if penv_path.exists():
        rmtree(penv_path)
    try:
        subprocess.check_call(
            ["uv", "venv", "--clear", f"--python={sys.executable}", penv_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        print(f"[Step 5] Created final venv: {penv_dir}")
    except Exception as e:
        print(f"Failed to create final venv: {e}", file=sys.stderr)
        sys.exit(1)
    # marker file
    Path(penv_dir, "pioenv_marker").write_text("required by platform\n")

    if has_internet_connection():
        python_bin = Path(penv_dir) / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
        if not install_dependencies(str(python_bin)):
            print("Failed to install Python dependencies", file=sys.stderr)
            sys.exit(1)
    else:
        print("No internet detected - skipping dependencies")

    python_bin = Path(penv_dir) / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
    subprocess.call([str(python_bin)] + args)
    sys.exit(0)

def setup_pipenv(env, penv_dir):
    python_bin = Path(penv_dir) / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
    if not python_bin.exists():
        uv_path = None
        try:
            python_exe = env.subst("$PYTHONEXE")
            uv_guess = Path(python_exe).parent / ("uv.exe" if IS_WINDOWS else "uv")
            uv_path = str(uv_guess) if uv_guess.exists() else "uv"
            subprocess.check_call(
                [uv_path, "venv", "--clear", f"--python={python_exe}", penv_dir],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            print(f"[Setup] Created venv using uv: {penv_dir}")
            Path(penv_dir, "pioenv_marker").write_text("required by platform\n")
            return uv_path
        except Exception:
            pass

        env.Execute(env.VerboseAction(
            f'"$PYTHONEXE" -m venv --clear "{penv_dir}"',
            f"Created python venv at {penv_dir}"))
        Path(penv_dir, "pioenv_marker").write_text("required by platform\n")
    return None

def setup_python_paths(penv_dir):
    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_package_dir = Path(penv_dir) / ("Lib" if IS_WINDOWS else "lib") / python_version / "site-packages"
    if site_package_dir.exists():
        import site
        site.addsitedir(str(site_package_dir))

def ensure_pip(python_executable):
    try:
        subprocess.check_call([python_executable, "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        try:
            subprocess.check_call([python_executable, "-m", "ensurepip", "--default-pip"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print(f"Failed to install pip: {e}", file=sys.stderr)
            return False

def install_dependencies(python_executable, uv_executable=None):
    if not ensure_pip(python_executable):
        print("pip not installed, aborting dependency install", file=sys.stderr)
        return False

    venv_dir = Path(python_executable).parent.parent
    uv_executable = uv_executable or get_executable_path(venv_dir, "uv")
    try:
        subprocess.check_call([uv_executable, "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        try:
            subprocess.check_call([python_executable, "-m", "pip", "install", "uv>=0.1.0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Failed to install uv: {e}", file=sys.stderr)
            return False

    def get_installed_packages():
        try:
            res = subprocess.run(
                [uv_executable, "pip", "list", f"--python={python_executable}", "--format=json"],
                capture_output=True, text=True, timeout=600)
            if res.returncode == 0:
                packages = json.loads(res.stdout)
                return {p["name"].lower(): p["version"] for p in packages}
        except Exception:
            pass
        return {}

    installed_packages = get_installed_packages()

    packages_to_install = []
    for pkg, spec in python_deps.items():
        if pkg.lower() not in installed_packages:
            packages_to_install.append(spec if spec.startswith(("http", "git+", "file://")) else f"{pkg}{spec}")

    if packages_to_install:
        try:
            subprocess.check_call([uv_executable, "pip", "install", "--quiet", "--upgrade", f"--python={python_executable}"] + packages_to_install,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
            print(f"Installed packages: {packages_to_install}")
        except Exception as e:
            print(f"Failed to install packages: {e}", file=sys.stderr)
            return False

    return True

def launch_penv(python_executable, args):
    try:
        subprocess.check_call([python_executable] + args)
    except subprocess.CalledProcessError as error:
        print(f"Error in launch_penv: {error}", file=sys.stderr)
        sys.exit(error.returncode)

def _install_pyos_tool(platform, python_executable, uv_executable):
    esptool_dir = platform.get_package_path("tool-esptoolpy")
    if not esptool_dir:
        return

    try:
        result = subprocess.run(
            [python_executable, "-c",
             "import esptool, os, sys; print('MATCH' if os.path.abspath(sys.argv[1]) == os.path.abspath(os.path.dirname(esptool.__file__)) else 'MISMATCH')",
             esptool_dir],
            capture_output=True, text=True, timeout=5)
        if result.stdout.strip() == "MATCH":
            return
    except Exception:
        pass

    try:
        subprocess.check_call([uv_executable, "pip", "install", "-e", esptool_dir, "--quiet"], timeout=60)
        print("Installed esptool in editable mode")
    except Exception as e:
        print(f"Failed to install esptool: {e}", file=sys.stderr)

def _install_pyos_tool(platform, python_executable, uv_executable):
    esptool_dir = platform.get_package_dir("tool-esptoolpy")
    if not esptool_dir:
        return

    try:
        result = subprocess.run([python_executable, "-c",
            "import esptool, os, sys; "
            "expected = os.path.normcase(os.path.realpath(sys.argv[1])); "
            "actual = os.path.normcase(os.path.realpath(os.path.dirname(esptool.__file__))); "
            "print('MATCH' if actual.startswith(expected) else 'MISMATCH')",
            esptool_dir], capture_output=True, text=True, timeout=5)
        if result.stdout.strip() == "MATCH":
            return
    except Exception:
        pass

    try:
        subprocess.check_call([uv_executable, "pip", "install", "--quiet", "--force-reinstall", f"--python={python_executable}", "-e", esptool_dir], timeout=60)
        print("Installed esptool via pip")
    except Exception as e:
        print(f"Failed to install esptool: {e}", file=sys.stderr)

def setup_python_paths(penv_dir):
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    if IS_WINDOWS:
        site_path = Path(penv_dir) / "Lib" / "site-packages"
    else:
        site_path = Path(penv_dir) / "lib" / version / "site-packages"
    if site_path.exists():
        import site
        site.addsitedir(str(site_path))

def setup_python_environment(env, platform, platform_dir, install_esptool=True):
    penv_dir = str(Path(platform_dir) / "penv")

    if "--in-temp" in sys.argv:
        idx = sys.argv.index("--in-temp")
        in_penv = sys.argv[idx + 1]
        in_temp = sys.argv[idx + 2]
        args = sys.argv[idx + 3:]
        in_temp_process(in_penv, in_temp, str(Path(__file__).absolute()), args)

    uv_exec = None
    if env:
        uv_exec = setup_pipenv(env, penv_dir)
    else:
        uv_exec = _setup_pipenv_minimal(penv_dir)

    python_executable = get_executable_path(penv_dir, "python")

    if env:
        env.Replace(PYTHONEXE=python_executable)

    if not Path(python_executable).exists():
        sys.stderr.write(f"Python executable not found: {python_executable}\n")
        sys.exit(1)

    setup_python_paths(penv_dir)

    uv_bin = get_executable_path(penv_dir, "uv")
    esptool_bin = get_executable_path(penv_dir, "esptool")

    if has_internet_connection() or bool(os.getenv("GITHUB_ACTIONS")):
        if not install_dependencies(python_executable, uv_exec):
            sys.stderr.write("Failed to install dependencies\n")
            sys.exit(1)

    if install_esptool:
        if env:
            # Use env-specific install if applicable
            platform.install_esptool(env, platform, python_executable, uv_bin)
        else:
            _install_pyos_tool(platform, python_executable, uv_bin)

    # Certifi etc env
    certifi_path = subprocess.check_output([python_executable, "-m", "certifi"], text=True, timeout=5).strip()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi_path
    os.environ["SSL_CERT_FILE"] = certifi_path
    if env:
        env.AppendENVPath("REQUESTS_CA_BUNDLE", certifi_path)
        env.AppendENVPath("SSL_CERT_FILE", certifi_path)

    return python_executable, esptool_bin


def _setup_pipenv_minimal(penv_dir):
    if not Path(get_executable_path(penv_dir, "python")).exists():
        python_executable = sys.executable
        uv_executable = None
        try:
            uv_guess = Path(python_executable).parent / ("uv.exe" if IS_WINDOWS else "uv")
            uv_executable = str(uv_guess) if uv_guess.exists() else "uv"
            subprocess.check_call([uv_executable, "venv", "--clear", f"--python={python_executable}", penv_dir], timeout=90)
            print(f"Created venv using uv: {penv_dir}")
            Path(penv_dir, "pioenv_marker").write_text("required by platform\n")
            return uv_executable
        except Exception:
            subprocess.check_call([python_executable, "-m", "venv", "--clear", penv_dir])
            print(f"Created classic venv: {penv_dir}")
            Path(penv_dir, "pioenv_marker").write_text("required by platform\n")
            return None
    return None
