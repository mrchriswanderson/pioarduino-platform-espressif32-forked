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
import re
import semantic_version
import site
import socket
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

from platformio.package.version import pepver_to_semver
from platformio.compat import IS_WINDOWS

if sys.version_info < (3, 10):
    sys.stderr.write(
        f"Error: Python 3.10 or higher is required. "
        f"Current version: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\n"
        f"Please update your Python installation.\n"
    )
    sys.exit(1)

github_actions = bool(os.getenv("GITHUB_ACTIONS"))

PLATFORMIO_URL_VERSION_RE = re.compile(
    r'/v?(\d+\.\d+\.\d+(?:[.-]\w+)?(?:\.\d+)?)(?:\.(?:zip|tar\.gz|tar\.bz2))?$',
    re.IGNORECASE,
)

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


def create_temp_uv_venv(current_python):
    temp_dir = tempfile.mkdtemp(prefix="penv_temp_")
    uv_cmd = "uv"
    try:
        subprocess.check_call([
            uv_cmd, "venv", "--clear", f"--python={current_python}", temp_dir
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    except Exception as e:
        sys.stderr.write(f"Error creating temp uv venv: {e}\n")
        sys.exit(1)
    return temp_dir


def launch_temp_venv(temp_dir, penv_dir, script_path, remaining_args):
    temp_python = Path(temp_dir) / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")

    if not temp_python.exists():
        sys.stderr.write(f"Temp python executable not found: {temp_python}\n")
        sys.exit(1)

    args = [
        str(temp_python),
        script_path,
        "--in-temp",
        penv_dir,
        temp_dir
    ] + remaining_args

    subprocess.Popen(args, close_fds=True)
    sys.exit(0)


def install_dependencies(python_executable):
    penv_dir = Path(python_executable).parent.parent
    uv_executable = get_executable_path(penv_dir, "uv")

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
        except subprocess.CalledProcessError as e:
            sys.stderr.write(f"Error installing uv: {e}\n")
            return False

    def get_installed_packages():
        try:
            cmd = [uv_executable, "pip", "list", f"--python={python_executable}", "--format=json"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                packages = json.loads(result.stdout)
                return {p["name"].lower(): p["version"] for p in packages}
        except Exception as e:
            sys.stderr.write(f"Warning: could not list installed packages: {e}\n")
        return {}

    installed_packages = get_installed_packages()
    to_install = []
    for pkg, spec in python_deps.items():
        lower = pkg.lower()
        if lower not in installed_packages:
            to_install.append(pkg + spec if not spec.startswith(("http://", "https://", "git+", "file://")) else spec)

    if to_install:
        try:
            subprocess.check_call(
                [uv_executable, "pip", "install", "--quiet", "--upgrade", f"--python={python_executable}"] + to_install,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600
            )
            print(f"[STEP 9] Installed/upgraded Python dependencies: {to_install}")
        except Exception as e:
            sys.stderr.write(f"Error installing dependencies: {e}\n")
            return False
    return True


def write_marker(penv_dir):
    marker_path = Path(penv_dir) / "pioarduino_py"
    marker_path.write_text("required by pioarduino\n")


def launch_pen_v_python(penv_dir, remaining_args):
    penv_python = Path(penv_dir) / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
    if not penv_python.exists():
        sys.stderr.write(f"Error: penv python does not exist at {penv_python}\n")
        sys.exit(1)
    cmd = [str(penv_python)] + remaining_args
    subprocess.call(cmd)


def in_temp_process(penv_dir, temp_dir, script_path, remaining_args):
    penv_path = Path(penv_dir)

    # Step 4: Remove old penv safely (temp python is running)
    if penv_path.exists():
        shutil.rmtree(penv_path)

    # Step 5: Create new penv uv environment
    uv_cmd = "uv"
    try:
        subprocess.check_call([
            uv_cmd, "venv", "--clear", f"--python={sys.executable}", penv_dir
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    except Exception as e:
        sys.stderr.write(f"Error recreating penv uv venv: {e}\n")
        sys.exit(1)

    # Step 6: Write marker
    write_marker(penv_dir)

    # Step 7/8: Install dependencies if possible
    if has_internet_connection():
        if not install_dependencies(str(penv_path / ("Scripts" if IS_WINDOWS else "bin") /
                                     ("python.exe" if IS_WINDOWS else "python"))):
            sys.stderr.write("Failed to install Python dependencies\n")
            sys.exit(1)
    else:
        print("No internet detected, skipping dependency installation")

    # Step 9: Launch penv python for further setup or user process
    launch_pen_v_python(penv_dir, remaining_args)
    sys.exit(0)


def setup_pipenv(env, penv_dir):
    if not os.path.isfile(get_executable_path(penv_dir, "python")):
        uv_success = False
        uv_cmd = None
        try:
            python_exe = env.subst("$PYTHONEXE")
            python_dir = os.path.dirname(python_exe)
            uv_exe_suffix = ".exe" if IS_WINDOWS else ""
            uv_cmd = str(Path(python_dir) / f"uv{uv_exe_suffix}")
            if not os.path.isfile(uv_cmd):
                uv_cmd = "uv"
            subprocess.check_call(
                [uv_cmd, "venv", "--clear", f"--python={python_exe}", penv_dir],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=90
            )
            uv_success = True
            print(f"Created pioarduino Python virtual environment using uv: {penv_dir}")
        except Exception:
            pass
        if not uv_success:
            uv_cmd = None
            env.Execute(env.VerboseAction(
                '"$PYTHONEXE" -m venv --clear "%s"' % penv_dir,
                "Created pioarduino Python virtual environment: %s" % penv_dir,
            ))
        penv_python = get_executable_path(penv_dir, "python")
        if not os.path.isfile(penv_python):
            sys.stderr.write(f"Error: Failed to create proper virtual environment at {penv_python}\n")
            sys.exit(1)
        write_marker(penv_dir)
        return uv_cmd if uv_success else None
    return None


def setup_python_paths(penv_dir):
    python_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = (
        str(Path(penv_dir) / "Lib" / "site-packages") if IS_WINDOWS
        else str(Path(penv_dir) / "lib" / python_ver / "site-packages")
    )
    if os.path.isdir(site_packages):
        site.addsitedir(site_packages)


def setup_python_paths(penv_dir):
    python_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = (
        str(Path(penv_dir) / "Lib" / "site-packages") if IS_WINDOWS
        else str(Path(penv_dir) / "lib" / python_ver / "site-packages")
    )
    if os.path.isdir(site_packages):
        site.addsitedir(site_packages)


def get_packages_to_install(deps, installed_packages):
    for package, spec in deps.items():
        name = package.lower()
        if name not in installed_packages:
            yield package
        elif name == "platformio":
            m = PLATFORMIO_URL_VERSION_RE.search(spec)
            if m:
                expected_ver = pepver_to_semver(m.group(1))
                if installed_packages.get(name) != expected_ver:
                    yield package
            else:
                continue
        else:
            version_spec = semantic_version.SimpleSpec(spec)
            if not version_spec.match(installed_packages[name]):
                yield package


def _setup_pipenv_minimal(penv_dir):
    """
    Setup penv without SCons. Creates venv via uv or fallback.
    """
    if not os.path.isfile(get_executable_path(penv_dir, "python")):
        uv_success = False
        uv_cmd = None
        try:
            python_dir = os.path.dirname(sys.executable)
            uv_exe_suffix = ".exe" if IS_WINDOWS else ""
            uv_cmd = str(Path(python_dir) / f"uv{uv_exe_suffix}")
            if not os.path.isfile(uv_cmd):
                uv_cmd = "uv"
            subprocess.check_call(
                [uv_cmd, "venv", "--clear", f"--python={sys.executable}", penv_dir],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=90
            )
            uv_success = True
            print(f"Created pioarduino Python virtual environment using uv: {penv_dir}")
        except Exception:
            pass
        if not uv_success:
            uv_cmd = None
            try:
                subprocess.check_call([sys.executable, "-m", "venv", "--clear", penv_dir])
                print(f"Created pioarduino Python virtual environment: {penv_dir}")
            except subprocess.CalledProcessError as e:
                sys.stderr.write(f"Error: Failed to create virtual environment: {e}\n")
                sys.exit(1)
        penv_python = get_executable_path(penv_dir, "python")
        if not os.path.isfile(penv_python):
            sys.stderr.write(f"Error: Virtual environment missing python at {penv_python}\n")
            sys.exit(1)
        write_marker(penv_dir)
        return uv_cmd if uv_success else None
    return None


def _install_esptool_from_tl_install(platform, python_exe, uv_executable):
    esptool_repo_path = platform.get_package_dir("tool-esptoolpy") or ""
    if not esptool_repo_path or not os.path.isdir(esptool_repo_path):
        return (None, None)
    try:
        result = subprocess.run(
            [python_exe, "-c",
             ("import esptool, os, sys; "
              "expected_path = os.path.normcase(os.path.realpath(sys.argv[1])); "
              "actual_path = os.path.normcase(os.path.realpath(os.path.dirname(esptool.__file__))); "
              "print('MATCH' if actual_path.startswith(expected_path) else 'MISMATCH')"),
             esptool_repo_path],
            capture_output=True, check=True, text=True, timeout=5)
        if result.stdout.strip() == "MATCH":
            return
    except Exception:
        pass
    try:
        subprocess.check_call([
            uv_executable, "pip", "install", "--quiet", "--force-reinstall",
            f"--python={python_exe}", "-e", esptool_repo_path
        ], timeout=60)
        print(f"Installed esptool from tl-install path: {esptool_repo_path}")
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to install esptool from {esptool_repo_path} (exit {e.returncode})")


def _setup_certifi_env(env, python_exe):
    try:
        out = subprocess.check_output(
            [python_exe, "-c", "import certifi; print(certifi.where())"],
            text=True, timeout=5)
        cert_path = out.strip()
    except Exception as e:
        print(f"Error: Failed to obtain certifi path from the virtual environment: {e}")
        return

    os.environ["CERTIFI_PATH"] = cert_path
    os.environ["SSL_CERT_FILE"] = cert_path
    os.environ["REQUESTS_CA_BUNDLE"] = cert_path
    os.environ["CURL_CA_BUNDLE"] = cert_path
    os.environ["GIT_SSL_CAINFO"] = cert_path

    if env is not None:
        env_vars = dict(env.get("ENV", {}))
        env_vars.update({
            "CERTIFI_PATH": cert_path,
            "SSL_CERT_FILE": cert_path,
            "REQUESTS_CA_BUNDLE": cert_path,
            "CURL_CA_BUNDLE": cert_path,
            "GIT_SSL_CAINFO": cert_path,
        })
        env.Replace(ENV=env_vars)


def _setup_python_environment_core(env, platform, platformio_dir, should_install_esptool=True):
    penv_dir = str(Path(platformio_dir) / "penv")

    # If current process is normal startup, perform temp venv upgrade logic
    if "--in-temp" in sys.argv:
        idx = sys.argv.index("--in-temp")
        # This branch is normally called from launch_temp_venv subprocess
        return in_temp_process(
            penv_dir,
            sys.argv[idx + 2],
            str(Path(__file__).absolute()),
            sys.argv[idx + 3:]
        )

    if "--final-setup" in sys.argv:
        # Optionally handle final installation steps here if-ever used
        # (not explicitly demanded here)
        pass

    # Normal startup in SCons or minimal environment
    if env is not None:
        # SCons version calls the function that can recreate penv if missing
        used_uv_executable = setup_pipenv_in_package(env, penv_dir)
    else:
        used_uv_executable = _setup_pipenv_minimal(penv_dir)

    penv_python = get_executable_path(penv_dir, "python")

    if env is not None:
        env.Replace(PYTHONEXE=penv_python)

    if not os.path.isfile(penv_python):
        sys.stderr.write(f"Error: Python executable not found: {penv_python}\n")
        sys.exit(1)

    setup_python_paths(penv_dir)

    esptool_binary_path = get_executable_path(penv_dir, "esptool")
    uv_executable = get_executable_path(penv_dir, "uv")

    if has_internet_connection() or github_actions:
        if not install_python_deps(penv_python):
            sys.stderr.write("Error: Failed to install Python dependencies into penv\n")
            sys.exit(1)
    else:
        print("Warning: No internet connection detected, Python dependency check will be skipped.")

    if should_install_esptool:
        if env is not None:
            install_esptool(env, platform, penv_python, uv_executable)
        else:
            _install_esptool_from_tl_install(platform, penv_python, uv_executable)

    _setup_certifi_env(env, penv_python)

    return penv_python, esptool_binary_path
